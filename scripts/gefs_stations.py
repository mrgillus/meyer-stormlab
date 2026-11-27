"""Station-dense GEFS reforecast extension for storm-total snowfall labels.

A separate, resumable archive (data/gefs_stations/). The existing GEFS
archives are never modified. Each shard holds, for one 00Z initialization and
five members, the 2 m and 850 mb temperature and the 24 h precipitation (from
exact 6 h blocks) at +24/+48/+72 h. Points are every GHCN snowfall station
within RADIUS_MI of St. Louis with enough 2000–2019 cold-season reports
(capped at MAX_STATIONS), so more storms have complete station reports
(see storm_total_labels.py).

Commands:
  select   Build the station list, GHCN observations and a frozen manifest.
           Refuses to run if a manifest exists, because points must never
           change under an in-progress archive.
  plan     Pick the next outcome-independent dates (year-interleaved calendar).
  worker   Extract the assigned dates (BATCH_DATES) to gefs-stations-output/.
  publish  Validate worker checkpoints and merge them into data/gefs_stations/.
"""
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import gefs_batch as b
import regional_archive as src

ROOT = b.ROOT
OUT = ROOT / "data/gefs_stations"
MEMBERS = b.MEMBERS
LEADS = (24, 48, 72)
FIELDS = ("t2m", "t850", "precip24")
SOURCES = {"t2m": ("tmp_2m", "TMP", "2 m above ground"), "t850": ("tmp_pres", "TMP", "850 mb")}
EXPECTED = {(m, l, f) for m in MEMBERS for l in LEADS for f in FIELDS}
RADIUS_MI = 75
MAX_STATIONS = 150
MIN_USABLE_DAYS = 400
CAP = 12
WORKERS = 8
MAX_ATTEMPTS = 3
DECIMALS = 2


# ---------- station selection ----------

def select():
    if (OUT / "manifest.json").exists():
        raise SystemExit("Manifest exists; station points are frozen for this archive.")
    candidates = []
    for line in src.fetch(src.NOAA + "ghcnd-inventory.txt").text.splitlines():
        v = line.split()
        if len(v) != 6 or v[3] != "SNOW" or not v[0].startswith("US"):
            continue
        ident, lat, lon, _, first, last = v
        lat, lon = float(lat), float(lon)
        # Any overlap with the 2000–2019 archive; usable-day count decides below.
        if int(last) < 2001 or int(first) > 2018 or src.distance(lat, lon) > RADIUS_MI:
            continue
        candidates.append((ident, lat, lon))
    names = {}
    for line in src.fetch(src.NOAA + "ghcnd-stations.txt").text.splitlines():
        if len(line) >= 71:
            names[line[:11]] = line[41:71].strip()

    def parse(c):
        ident, lat, lon = c
        rows = []
        for line in src.fetch(src.NOAA + "all/" + ident + ".dly").text.splitlines():
            if line[17:21] != "SNOW":
                continue
            y, m = int(line[11:15]), int(line[15:17])
            if y not in src.YEARS or m not in src.MONTHS:
                continue
            for i in range(31):
                s = line[21 + 8 * i:29 + 8 * i]
                if len(s) != 8:
                    continue
                try:
                    day = dt.date(y, m, i + 1).isoformat()
                    value = int(s[:5])
                except ValueError:
                    continue
                # Same quality rules as regional_archive: no QC flag, no P/A measurement flags.
                if value < 0 or s[6] != " " or s[5] in ("P", "A"):
                    continue
                rows.append((ident, day, value, s[5], s[7]))
        return dict(id=ident, name=names.get(ident, ident), lat=lat, lon=lon,
                    distance_miles=round(src.distance(lat, lon), 1), usable_days=len(rows),
                    days_ge_1in=sum(r[2] >= 25.4 for r in rows)), rows

    parsed, errors = [], []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for c, f in [(c, ex.submit(parse, c)) for c in candidates]:
            try:
                parsed.append(f.result())
            except Exception as e:
                errors.append(dict(station=c[0], error=str(e)[:200]))
    parsed = [p for p in parsed if p[0]["usable_days"] >= MIN_USABLE_DAYS]
    # Most usable reports first; ties by distance. No snowfall amounts in the ranking.
    parsed.sort(key=lambda p: (-p[0]["usable_days"], p[0]["distance_miles"], p[0]["id"]))
    parsed = parsed[:MAX_STATIONS]
    stations = [p[0] for p in parsed]
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT / "observations.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station", "reported_date", "snow_mm", "measurement_flag", "source_flag"])
        for _, rows in parsed:
            w.writerows(sorted(rows))
    (OUT / "stations.json").write_text(json.dumps(dict(
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(), radius_mi=RADIUS_MI,
        min_usable_days=MIN_USABLE_DAYS, candidates=len(candidates), errors=errors, stations=stations), indent=2) + "\n")
    m = dict(schema=1, archive="NOAA GEFSv12 reforecast 2000–2019, station-dense extension",
             members=list(MEMBERS), lead_hours=list(LEADS), fields=list(FIELDS), decimals=DECIMALS,
             points=[dict(label=s["id"], lat=s["lat"], lon=s["lon"]) for s in stations])
    m["fingerprint"] = hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
    b.atomic_json(OUT / "manifest.json", m)
    print("Selected", len(stations), "of", len(candidates), "candidates;", len(errors), "errors", flush=True)


# ---------- archive ----------

def manifest():
    m = json.loads((OUT / "manifest.json").read_text())
    if hashlib.sha256(json.dumps({k: v for k, v in m.items() if k != "fingerprint"}, sort_keys=True).encode()).hexdigest() != m["fingerprint"]:
        raise ValueError("Manifest fingerprint mismatch")
    if tuple(m["members"]) != MEMBERS or tuple(m["lead_hours"]) != LEADS or tuple(m["fields"]) != FIELDS:
        raise ValueError("Manifest incompatible with extractor")
    return m


def path(date):
    return OUT / "shards" / date[:4] / (date + ".json.gz")


def validate(s, m):
    if s.get("schema") != 1 or s.get("fingerprint") != m["fingerprint"] or not 1 <= s.get("attempts", 0) <= MAX_ATTEMPTS:
        raise ValueError("Invalid shard provenance")
    keys = set()
    for r in s["rows"]:
        k = (r["member"], r["lead_hours"], r["field"])
        if k not in EXPECTED or k in keys:
            raise ValueError("Unexpected or duplicate feature")
        if len(r["values"]) != len(m["points"]) or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in r["values"]):
            raise ValueError("Invalid values")
        if r["field"] == "precip24" and min(r["values"]) < -0.001:
            raise ValueError("Negative precipitation")
        if r["field"] != "precip24" and not all(180 < v < 340 for v in r["values"]):
            raise ValueError("Temperature not in Kelvin range")
        keys.add(k)
    if s["core_complete"] != (keys == EXPECTED):
        raise ValueError("Coverage flag mismatch")
    return s


def load(m):
    out = {}
    for p in OUT.glob("shards/*/*.json.gz"):
        s = validate(b.read(p), m)
        if p != path(s["date"]):
            raise ValueError("Shard path mismatch")
        out[s["date"]] = s
    return out


def plan():
    m = manifest()
    old = load(m)
    dates = b.calendar()
    retry = [d for d in dates if d in old and not old[d]["core_complete"] and old[d]["attempts"] < MAX_ATTEMPTS]
    new = [d for d in dates if d not in old]
    queue = (retry[:WORKERS] + new + retry[WORKERS:])[:CAP * WORKERS]
    jobs = [dict(worker=i, dates=",".join(queue[i::WORKERS])) for i in range(WORKERS) if queue[i::WORKERS]]
    Path("gefs-stations-plan.json").write_text(json.dumps(dict(dates=queue, fingerprint=m["fingerprint"], workers=WORKERS)))
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write("matrix=" + json.dumps({"include": jobs}, separators=(",", ":")) + "\n")
        f.write("has_work=" + ("true" if jobs else "false") + "\n")
    print("Planned", len(queue), "dates;", sum(s["core_complete"] for s in old.values()), "complete of", len(dates), flush=True)


def checked(date, url, rec, points, end):
    d = src.decode(url, rec, points)
    if d["data_date"] != int(date) or d["data_time"] != 0 or d["end_step"] != end:
        raise ValueError("GRIB initialization/lead mismatch")
    return d


def extract(date, member, points, existing):
    rows, errors = [], []
    for name, (file, var, level) in SOURCES.items():
        todo = [l for l in LEADS if (member, l, name) not in existing]
        if not todo:
            continue
        try:
            url = src.file_url(date, member, 24, file)
            idx = src.index(url)
            for lead in todo:
                try:
                    match = [r for r in idx if r[2][3] == var and r[2][4] == level and r[2][5].strip() == f"{lead} hour fcst"]
                    if len(match) != 1:
                        raise ValueError(f"Expected 1 message, got {len(match)}")
                    d = checked(date, url, match[0], points, lead)
                    if d["units"] != "K":
                        raise ValueError("Unexpected temperature units " + d["units"])
                    rows.append(dict(member=member, lead_hours=lead, field=name, units="K",
                                     values=[round(v, DECIMALS) for v in d["values"]]))
                except Exception as e:
                    errors.append(dict(member=member, field=name, lead=lead, error=str(e)[:200]))
        except Exception as e:
            errors.append(dict(member=member, field=name, error=str(e)[:200]))
    todo = [l for l in LEADS if (member, l, "precip24") not in existing]
    if todo:
        try:
            url = src.file_url(date, member, 24, "apcp_sfc")
            idx = src.index(url)
            for lead in todo:
                try:
                    blocks = []
                    for end in range(lead - 18, lead + 1, 6):
                        match = [r for r in idx if r[2][3] == "APCP" and r[2][5].strip() == f"{end-6}-{end} hour acc fcst"]
                        if len(match) != 1:
                            raise ValueError(f"Missing exact {end-6}-{end}h block")
                        d = checked(date, url, match[0], points, end)
                        if d["units"] not in ("kg m**-2", "kg m-2", "mm"):
                            raise ValueError("Unexpected precipitation units " + d["units"])
                        blocks.append(d["values"])
                    rows.append(dict(member=member, lead_hours=lead, field="precip24", units="mm water equivalent",
                                     values=[round(max(0.0, sum(v)), DECIMALS) if sum(v) > -0.001 else sum(v) for v in zip(*blocks)]))
                except Exception as e:
                    errors.append(dict(member=member, field="precip24", lead=lead, error=str(e)[:200]))
        except Exception as e:
            errors.append(dict(member=member, field="precip24", error=str(e)[:200]))
    return rows, errors


def run_date(date, m, points, dest):
    old = b.read(path(date)) if path(date).exists() else {}
    if old.get("core_complete") or old.get("attempts", 0) >= MAX_ATTEMPTS:
        return None
    merged = {(r["member"], r["lead_hours"], r["field"]): r for r in old.get("rows", [])}
    errors = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for f in [ex.submit(extract, date, mem, points, dict(merged)) for mem in MEMBERS]:
            rows, err = f.result()
            errors.extend(err)
            for r in rows:
                merged[(r["member"], r["lead_hours"], r["field"])] = r
    s = dict(schema=1, date=date, fingerprint=m["fingerprint"], attempts=old.get("attempts", 0) + 1,
             rows=[merged[k] for k in sorted(merged)], errors=errors[:50], core_complete=set(merged) == EXPECTED)
    b.save(dest / (date + ".json.gz"), validate(s, m))
    return s


def worker():
    m = manifest()
    points = [(p["lat"], p["lon"]) for p in m["points"]]
    dates = os.environ["BATCH_DATES"].split(",")
    if len(dates) > CAP or len(set(dates)) != len(dates) or not set(dates) <= set(b.calendar()):
        raise ValueError("Invalid assignment")
    dest = Path("gefs-stations-output")
    dest.mkdir(exist_ok=True)
    start = time.monotonic()
    for date in dates:
        if time.monotonic() - start > 28 * 60:
            break
        s = run_date(date, m, points, dest)
        if s:
            print(date, len(s["rows"]), "/", len(EXPECTED), "errors", len(s["errors"]), flush=True)


def publish():
    m = manifest()
    plan = json.loads(Path("gefs-stations-plan.json").read_text())
    if plan["fingerprint"] != m["fingerprint"]:
        raise ValueError("Plan/manifest mismatch")
    old = load(m)
    received = set()
    for p in Path("gefs-stations-artifacts").rglob("*.json.gz"):
        s = validate(b.read(p), m)
        date = s["date"]
        if date not in plan["dates"] or date in received or p.name != date + ".json.gz":
            raise ValueError("Invalid worker checkpoint")
        received.add(date)
        prev = old.get(date)
        if prev and prev["attempts"] >= s["attempts"]:
            continue
        if prev and not {(r["member"], r["lead_hours"], r["field"]) for r in prev["rows"]} <= {(r["member"], r["lead_hours"], r["field"]) for r in s["rows"]}:
            raise ValueError("Checkpoint would lose rows")
        b.save(path(date), s)
    if not received:
        raise ValueError("No checkpoints recovered")
    shards = load(m)
    b.atomic_json(OUT / "progress.json", dict(
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(), fingerprint=m["fingerprint"],
        stations=len(m["points"]), complete=sum(s["core_complete"] for s in shards.values()),
        attempted=len(shards), received_this_run=len(received), total_initializations=len(b.calendar()),
        exhausted=sum(not s["core_complete"] and s["attempts"] >= MAX_ATTEMPTS for s in shards.values()),
        note="Station-dense +24/+48/+72h t2m, t850, precip24 extension; existing archives untouched."))


if __name__ == "__main__":
    {"select": select, "plan": plan, "worker": worker, "publish": publish}[sys.argv[1]]()
