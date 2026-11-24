"""Bounded, resumable GEFS cold-season archive. No outcome-based sampling.
Each shard contains verified GRIB fields for one initialization; never overwrite pilot.
"""
import collections
import datetime as dt
import gzip
import functools
import hashlib
import json
import pathlib
import random
import time
from concurrent.futures import ThreadPoolExecutor
import regional_archive as source

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"gefs_archive"
MEMBERS=("c00","p01","p02","p03","p04")
FIELDS=("t2m","t850","z500","mslp","pwat","precip24")
CORE=(24,72,168)
LEADS=(*CORE,336)
MAX_DATES=12
MAX_ATTEMPTS=3
# Reuse each forecast index within a batch, especially precipitation lead windows.
source.index=functools.lru_cache(maxsize=128)(source.index)

def read(p):
    with gzip.open(p,"rt") as f: return json.load(f)

def save(p,data):
    p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix(".tmp")
    # Deterministic gzip for reproducible file identity.
    temp.write_bytes(gzip.compress(json.dumps(data,separators=(",",":"),allow_nan=False).encode(),mtime=0))
    temp.replace(p)

def atomic_json(p,data):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(".tmp");t.write_text(json.dumps(data,indent=2,allow_nan=False)+"\n");t.replace(p)

def calendar():
    # Includes September lead-in for October targets. All years interleaved;
    # no observations or snowfall amounts enter the sampling decision.
    groups=[]
    for year in range(2000,2020):
        d=dt.date(year,1,1); group=[]
        while d.year==year:
            if d.month in (1,2,3,10,11,12) or (d.month==9 and d.day>=17):
                group.append(d.strftime("%Y%m%d"))
            d+=dt.timedelta(days=1)
        random.Random(137+year).shuffle(group)
        groups.append(group)
    return [g[i] for i in range(max(map(len,groups))) for g in groups if i<len(g)]

def key(row):
    return (row["member"],row["lead_hours"],row["field"])

def expected(leads):
    return {(m,l,f) for m in MEMBERS for l in leads for f in FIELDS}

def valid(row,date,point_count):
    if row["init"]!=date+"T00Z" or key(row) not in expected(LEADS):
        raise ValueError("Incorrect initialization or feature identity")
    if len(row["values"])!=point_count: raise ValueError("Incorrect point count")
    if not all(isinstance(v,(int,float)) and source.math.isfinite(v) for v in row["values"]):
        raise ValueError("Invalid field value")

def build(date,old,points):
    merged={key(r):r for r in old.get("rows",[])}; errors=[]
    incomplete=[m for m in MEMBERS if not all((m,l,f) in merged for l in LEADS for f in FIELDS)]
    # Existing decoder independently checks initialization and lead time.
    with ThreadPoolExecutor(max_workers=4) as ex:
        for m, future in [(m,ex.submit(source.extract,date,m)) for m in incomplete]:
            try:
                rows,err=future.result()
                errors.extend(err)
                for row in rows:
                    valid(row,date,len(points));merged[key(row)]=row
            except Exception as e: errors.append({"date":date,"member":m,"error":str(e)})
    keys=set(merged)
    return dict(schema=1,date=date,attempts=old.get("attempts",0)+1,
        rows=[merged[k] for k in sorted(keys)],errors=errors,
        core_complete=expected(CORE)<=keys,extended_complete=expected(LEADS)<=keys,
        core_rows=len(keys&expected(CORE)),extended_rows=len(keys&expected((336,))))

def main():
    stations=json.loads((ROOT/"data/regional/stations.json").read_text())
    # Fixed layout and provenance; local points are nearest-grid samples, not downscaling.
    points=[dict(label=f"synoptic_{i}",lat=lat,lon=lon) for i,(lat,lon) in enumerate(source.POINTS)]
    points.extend(dict(label=s["id"],lat=s["latitude"],lon=s["longitude"]) for s in stations if s["usable_days"]>0)
    meta=dict(schema=1,points=points,members=MEMBERS,lead_hours=LEADS,fields=FIELDS,
              archive="NOAA GEFSv12 2000–2019",selection="All cold-season initializations plus September lead-in, deterministic year-interleaved order.")
    sig=hashlib.sha256(json.dumps(meta,sort_keys=True).encode()).hexdigest()
    meta["fingerprint"]=sig
    manifest=OUT/"manifest.json"
    if manifest.exists() and json.loads(manifest.read_text())["fingerprint"]!=sig:
        raise RuntimeError("Point layout changed: create a new archive version rather than mixing features")
    atomic_json(manifest,meta)
    source.POINTS=[(p["lat"],p["lon"]) for p in points]
    dates=calendar()
    shards={}
    for p in (OUT/"shards").glob("*/*.json.gz"):
        d=read(p)
        if d.get("fingerprint")!=sig: raise ValueError("Shard schema mismatch "+str(p))
        shards[d["date"]]=d
    new=[d for d in dates if d not in shards]
    retry=[d for d in dates if d in shards and not shards[d]["core_complete"] and shards[d]["attempts"]<MAX_ATTEMPTS]
    queue=(retry[:1]+new+retry[1:])[:MAX_DATES]
    started=time.monotonic()
    for date in queue:
        if time.monotonic()-started>20*60: break
        data=build(date,shards.get(date,{}),points);data["fingerprint"]=sig
        path=OUT/"shards"/date[:4]/(date+".json.gz")
        save(path,data);shards[date]=data
        print(date,"core",data["core_rows"],"/",len(expected(CORE)),
              "extended",data["extended_rows"],"/",len(expected((336,))),flush=True)
    coverage=collections.Counter(d[:4] for d in dates)
    core=collections.Counter(d[:4] for d,v in shards.items() if v["core_complete"])
    attempted=collections.Counter(d[:4] for d in shards)
    errors=collections.Counter("not_found" if "404" in e.get("error","") else "other" for v in shards.values() for e in v["errors"])
    report=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        total_initializations=len(dates),attempted_initializations=len(shards),
        core_complete=sum(v["core_complete"] for v in shards.values()),
        extended_complete=sum(v["extended_complete"] for v in shards.values()),
        feature_rows=sum(len(v["rows"]) for v in shards.values()),
        point_count=len(points),member_count=len(MEMBERS),max_dates_per_run=MAX_DATES,
        core_leads=CORE,extended_lead=336,errors=dict(errors),
        exhausted_core=sum(not v["core_complete"] and v["attempts"]>=MAX_ATTEMPTS for v in shards.values()),
        by_year=[dict(year=y,total=coverage[y],attempted=attempted[y],core_complete=core[y]) for y in sorted(coverage)],
        training_ready=False,training_blockers=["Observation windows must be verified and aligned before joining labels.","Independent winter-season validation and adequate event counts are required."],
        note="14-day availability is tracked separately; missing files are not zeros. Incomplete core shards are retried up to three times.")
    atomic_json(OUT/"progress.json",report)
    print(json.dumps(report),flush=True)
if __name__=="__main__": main()
