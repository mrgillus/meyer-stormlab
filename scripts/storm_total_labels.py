"""Clock-independent historical storm-total snowfall labels (research).

Daily station snowfall reports cover 24 h ending at an unverified local
observation time, so daily labels cannot be aligned with GEFS UTC days (the
"two date mappings" problem). This builds event-total labels that need no
clock.

Coverage argument (local time = UTC - 5 or 6 h):
  A report dated D (local) covers (D-1 at c, D at c] with 0 < c <= 24 h, i.e.
  UTC (D-1+c', D+c'] with c' = c + offset, 5/24 < c' <= 30/24 days. Reports
  dated S-1..E+1 then cover UTC (S-2+c', E+1+c']. That always contains the
  UTC window [S, E+1), and never extends beyond UTC days S-2..E+2.

Event selection (per station, forecast-only):
  P[d] = 5-member mean GEFS +24h precipitation for UTC day d (init d 00Z), the
  shortest-range archived estimate. A storm is a maximal run of 1..MAX_DAYS
  consecutive UTC days with P >= WET_MM, with P <= DRY_MM on each of the two
  days before and after (S-2, S-1, E+1, E+2), all present. Observations are
  never used for selection, so the residual clock ambiguity is bounded by the
  forecast on those four dry days.

Label: the sum of reports S-1..E+1. Each must be present and measured or
trace; trace counts 0 (upper bound +0.05 in per trace day is tracked).

Features for learning come from init S (+24/+48/+72 h = UTC days S..S+2), so
the window must be at most 3 days. Selection depends on forecast wetness, so
events the forecast missed entirely are not represented. Live use applies the
same selection.
"""
import csv
import datetime as dt
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "data/gefs_archive"
ARCH48 = ROOT / "data/gefs48"
OBS = ROOT / "data/regional/observations.csv.gz"
OUT = ROOT / "data/storm_total_labels"
MEMBERS = ("c00", "p01", "p02", "p03", "p04")
FIELDS = ("t2m", "t850", "z500", "mslp", "pwat", "precip24")
WET_MM = 1.0
DRY_MM = 0.25
MAX_DAYS = 3
TRACE_UPPER_IN = 0.05
SPLITS = {"train": range(2000, 2013), "validation": range(2013, 2016), "test": range(2016, 2019)}


def season(d):
    return d.year if d.month >= 9 else d.year - 1


def split(d):
    y = season(d)
    return next((k for k, r in SPLITS.items() if y in r), None)


def load_shards(arch, leads):
    """{init date: {lead: array(fields, members, points)}} for complete shards."""
    manifest = json.loads((arch / "manifest.json").read_text())
    out = {}
    digest = hashlib.sha256()
    for path in sorted((arch / "shards").glob("*/*.json.gz")):
        with gzip.open(path, "rt") as f:
            s = json.load(f)
        if s.get("fingerprint") != manifest["fingerprint"] or not s.get("core_complete"):
            continue
        digest.update(path.read_bytes())
        look = {(r["member"], r["lead_hours"], r["field"]): r["values"] for r in s["rows"]}
        init = dt.datetime.strptime(s["date"], "%Y%m%d").date()
        for lead in leads:
            try:
                a = np.array([[look[(m, lead, f)] for m in MEMBERS] for f in FIELDS], float)
            except KeyError:
                continue
            if np.isfinite(a).all() and (a[5] > -0.001).all():
                out.setdefault(init, {})[lead] = a
    return manifest, out, digest.hexdigest()


def load_obs():
    obs = {}
    with gzip.open(OBS, "rt", newline="") as f:
        for r in csv.DictReader(f):
            flag = r["measurement_flag"].strip()
            obs[(r["station"], r["reported_date"])] = ("trace", 0.0) if flag == "T" else ("measured", float(r["snow_mm"]) / 25.4)
    return obs


def find_events(P):
    """P: {utc date: mm}. Yield (start, end) storm runs bracketed by dry days."""
    days = sorted(P)
    i = 0
    while i < len(days):
        d = days[i]
        if P[d] < WET_MM:
            i += 1
            continue
        s = e = d
        while (e + dt.timedelta(days=1)) in P and P[e + dt.timedelta(days=1)] >= WET_MM:
            e += dt.timedelta(days=1)
        i = days.index(e) + 1
        if (e - s).days + 1 > MAX_DAYS:
            continue
        ring = [s - dt.timedelta(days=2), s - dt.timedelta(days=1), e + dt.timedelta(days=1), e + dt.timedelta(days=2)]
        if all(r in P and P[r] <= DRY_MM for r in ring):
            yield s, e, round(sum(P[r] for r in ring), 3)


def label(obs, station, s, e):
    total, traces = 0.0, 0
    for k in range((e - s).days + 3):
        d = (s - dt.timedelta(days=1) + dt.timedelta(days=k)).isoformat()
        rec = obs.get((station, d))
        if rec is None:
            return None
        if rec[0] == "trace":
            traces += 1
        else:
            total += rec[1]
    return round(total, 3), traces


def mapping_label(obs, station, s, e, offset):
    """The legacy daily-mapping equivalent: sum reports for UTC days shifted by offset."""
    total = 0.0
    for k in range((e - s).days + 1):
        rec = obs.get((station, (s + dt.timedelta(days=k + offset)).isoformat()))
        if rec is None:
            return None
        total += rec[1]
    return round(total, 3)


def build():
    manifest, core, digest = load_shards(ARCH, (24, 72))
    _, ext48, digest48 = load_shards(ARCH48, (48,))
    obs = load_obs()
    points = manifest["points"]
    stations = [(i, p["label"]) for i, p in enumerate(points) if i >= 10 and any(k[0] == p["label"] for k in obs)]
    rows, rejected = [], Counter()
    for i, station in stations:
        P = {d: float(v[24][5, :, i].mean()) for d, v in core.items() if 24 in v}
        for s, e, ring in find_events(P):
            g = split(s)
            if g is None or split(e) != g:
                rejected["outside_split_seasons"] += 1
                continue
            lab = label(obs, station, s, e)
            if lab is None:
                rejected["incomplete_reports"] += 1
                continue
            n = (e - s).days + 1
            needed = {1: (24,), 2: (24, 48), 3: (24, 48, 72)}[n]
            have = {**core.get(s, {}), **ext48.get(s, {})}
            features_ok = all(l in have for l in needed)
            rows.append(dict(station=station, point_index=i, start_utc=s.isoformat(), end_utc=e.isoformat(), days=n,
                             split=g, observed_in=lab[0], trace_days=lab[1],
                             observed_upper_in=round(lab[0] + lab[1] * TRACE_UPPER_IN, 3),
                             dry_ring_forecast_mm=ring, window_forecast_mm=round(sum(P[s + dt.timedelta(days=k)] for k in range(n)), 3),
                             same_init_features=features_ok,
                             # regional_learning labels UTC day d (window ending d+1 00Z) with the report
                             # dated d+1 (utc_end_date) or d (utc_end_previous_date).
                             legacy_utc_end_date_in=mapping_label(obs, station, s, e, 1),
                             legacy_utc_end_previous_date_in=mapping_label(obs, station, s, e, 0)))
    return manifest, rows, rejected, digest, digest48


STATIONS = ROOT / "data/gefs_stations"
STATION_FIELDS = ("t2m", "t850", "precip24")


def load_station_archive():
    """{init: {lead: array(3 fields, members, points)}} from the station-dense extension."""
    manifest = json.loads((STATIONS / "manifest.json").read_text())
    if tuple(manifest["fields"]) != STATION_FIELDS or tuple(manifest["members"]) != MEMBERS:
        raise ValueError("Unexpected station archive schema")
    out, digest = {}, hashlib.sha256()
    for path in sorted((STATIONS / "shards").glob("*/*.json.gz")):
        with gzip.open(path, "rt") as f:
            s = json.load(f)
        if s.get("fingerprint") != manifest["fingerprint"] or not s.get("core_complete"):
            continue
        digest.update(path.read_bytes())
        look = {(r["member"], r["lead_hours"], r["field"]): r["values"] for r in s["rows"]}
        init = dt.datetime.strptime(s["date"], "%Y%m%d").date()
        for lead in manifest["lead_hours"]:
            a = np.array([[look[(m, lead, f)] for m in MEMBERS] for f in STATION_FIELDS], float)
            if np.isfinite(a).all() and (a[2] > -0.001).all():
                out.setdefault(init, {})[lead] = a
    return manifest, out, digest.hexdigest()


def load_obs_from(path):
    global OBS
    saved, OBS = OBS, path
    try:
        return load_obs()
    finally:
        OBS = saved


def build_stations():
    """Same selection and labels as build(), over all station-dense archive points."""
    manifest, arch, digest = load_station_archive()
    obs = load_obs_from(STATIONS / "observations.csv.gz")
    rows, rejected = [], Counter()
    for i, p in enumerate(manifest["points"]):
        station = p["label"]
        P = {d: float(v[24][2, :, i].mean()) for d, v in arch.items() if 24 in v}
        for s, e, ring in find_events(P):
            g = split(s)
            if g is None or split(e) != g:
                rejected["outside_split_seasons"] += 1
                continue
            lab = label(obs, station, s, e)
            if lab is None:
                rejected["incomplete_reports"] += 1
                continue
            n = (e - s).days + 1
            rows.append(dict(station=station, point_index=i, start_utc=s.isoformat(), end_utc=e.isoformat(), days=n,
                             split=g, observed_in=lab[0], trace_days=lab[1],
                             observed_upper_in=round(lab[0] + lab[1] * TRACE_UPPER_IN, 3),
                             dry_ring_forecast_mm=ring, window_forecast_mm=round(sum(P[s + dt.timedelta(days=k)] for k in range(n)), 3),
                             same_init_features=all(l in arch.get(s, {}) for l in (24, 48, 72)[:n]),
                             legacy_utc_end_date_in=mapping_label(obs, station, s, e, 1),
                             legacy_utc_end_previous_date_in=mapping_label(obs, station, s, e, 0)))
    return manifest, rows, rejected, digest, len(arch)


def summarize(rows):
    def cls(v):
        return "0" if v == 0 else "<1in" if v < 1 else "1-4in" if v < 4 else "4-6in" if v < 6 else ">=6in"
    out = {}
    for g in SPLITS:
        rr = [r for r in rows if r["split"] == g]
        usable = [r for r in rr if r["same_init_features"]]
        events = {(r["start_utc"], r["end_utc"]) for r in usable if r["observed_in"] >= 1}
        out[g] = dict(station_events=len(rr), with_same_init_features=len(usable),
                      by_observed=dict(Counter(cls(r["observed_in"]) for r in usable)),
                      by_days=dict(Counter(r["days"] for r in usable)),
                      distinct_snow_windows_ge1in=len(events),
                      distinct_snow_windows_ge4in=len({(r["start_utc"], r["end_utc"]) for r in usable if r["observed_in"] >= 4}),
                      stations=len({r["station"] for r in usable}))
    return out


def legacy_comparison(rows):
    """How much the two legacy daily mappings disagree with the clock-independent label."""
    out = {}
    for key in ("legacy_utc_end_date_in", "legacy_utc_end_previous_date_in"):
        pairs = [(r["observed_in"], r[key]) for r in rows if r[key] is not None and r["observed_in"] >= 1]
        if not pairs:
            continue
        diff = np.array([b - a for a, b in pairs])
        out[key] = dict(n_snow_events=len(pairs), mean_diff_in=round(float(diff.mean()), 3),
                        mean_abs_diff_in=round(float(np.abs(diff).mean()), 3),
                        share_missing_over_half=round(float(np.mean([b < 0.5 * a for a, b in pairs])), 3))
    return out


SENSITIVITY = ((1.0, 0.5), (2.0, 0.25), (1.0, 1.0))


def sensitivity():
    """Sample size under alternative thresholds (diagnostic only; labels use the defaults)."""
    global WET_MM, DRY_MM
    saved, out = (WET_MM, DRY_MM), []
    try:
        for WET_MM, DRY_MM in SENSITIVITY:
            rows = build()[1]
            out.append(dict(wet_mm=WET_MM, dry_mm=DRY_MM, splits={g: {k: v[k] for k in (
                "station_events", "distinct_snow_windows_ge1in", "distinct_snow_windows_ge4in")} for g, v in summarize(rows).items()}))
    finally:
        WET_MM, DRY_MM = saved
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest, rows, rejected, digest, digest48 = build()
    with gzip.open(OUT / "labels.csv.gz", "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    report = dict(schema=1, generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  archive_fingerprint=manifest["fingerprint"], archive_snapshot_sha256=digest,
                  archive48_snapshot_sha256=digest48,
                  parameters=dict(wet_mm=WET_MM, dry_mm=DRY_MM, max_days=MAX_DAYS, trace_upper_in=TRACE_UPPER_IN,
                                  selection_field="5-member mean GEFS +24h precip24 (UTC day)",
                                  observed_reports="local dates S-1..E+1"),
                  rejected=dict(rejected), splits=summarize(rows), legacy_mapping_comparison=legacy_comparison(rows),
                  threshold_sensitivity=sensitivity(),
                  limitations=["Selection uses forecast wetness; storms the +24h forecast missed are absent.",
                               "Residual clock ambiguity bounded by forecast precip on the 4 dry days (<= 4 x 0.25 mm liquid).",
                               "Station events in the same storm are correlated; distinct windows are the effective sample.",
                               "GHCN snowfall only; no snow-water-equivalent or phase observations."])
    (OUT / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: report[k] for k in ("rejected", "splits", "legacy_mapping_comparison")}, indent=2))


def main_stations():
    out = ROOT / "data/storm_total_labels_stations"
    out.mkdir(parents=True, exist_ok=True)
    manifest, rows, rejected, digest, inits = build_stations()
    with gzip.open(out / "labels.csv.gz", "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    report = dict(schema=1, generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  archive="data/gefs_stations", archive_fingerprint=manifest["fingerprint"], archive_snapshot_sha256=digest,
                  complete_initializations=inits, stations=len(manifest["points"]),
                  parameters=dict(wet_mm=WET_MM, dry_mm=DRY_MM, max_days=MAX_DAYS, trace_upper_in=TRACE_UPPER_IN),
                  rejected=dict(rejected), splits=summarize(rows), legacy_mapping_comparison=legacy_comparison(rows),
                  distinct_windows_all_seasons=dict(
                      ge1in=len({(r["start_utc"], r["end_utc"]) for r in rows if r["observed_in"] >= 1}),
                      ge4in=len({(r["start_utc"], r["end_utc"]) for r in rows if r["observed_in"] >= 4}),
                      ge6in=len({(r["start_utc"], r["end_utc"]) for r in rows if r["observed_in"] >= 6})))
    (out / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: report[k] for k in ("complete_initializations", "rejected", "splits", "distinct_windows_all_seasons", "legacy_mapping_comparison")}, indent=1))


if __name__ == "__main__":
    import sys
    main_stations() if sys.argv[1:] == ["stations"] else main()
