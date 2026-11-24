"""Parallel GEFS compilation: deterministic work allocation, independent artifacts,
single validated publisher. Reuses the verified archive schema and extraction code.
"""
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import time
import gefs_batch as b

WORKERS=8
DATES_PER_WORKER=12
BUDGET_SECONDS=32*60
OUT=b.OUT

def manifest():
    m=json.loads((OUT/"manifest.json").read_text())
    sig=m["fingerprint"]
    if hashlib.sha256(json.dumps({k:v for k,v in m.items() if k!="fingerprint"},sort_keys=True).encode()).hexdigest()!=sig:
        raise ValueError("Manifest fingerprint mismatch")
    if (tuple(m["members"])!=b.MEMBERS or tuple(m["fields"])!=b.FIELDS
        or tuple(m["lead_hours"])!=b.LEADS):
        raise ValueError("Manifest incompatible with decoder")
    return m

def shard_path(date):
    return OUT/"shards"/date[:4]/(date+".json.gz")

def check_shard(d,m):
    if d["fingerprint"]!=m["fingerprint"] or d["schema"]!=1 or not 1<=d["attempts"]<=b.MAX_ATTEMPTS:
        raise ValueError("Invalid shard provenance")
    keys=set()
    for r in d["rows"]:
        b.valid(r,d["date"],len(m["points"]))
        k=b.key(r)
        if k in keys:raise ValueError("Duplicate field")
        keys.add(k)
    if d["core_complete"]!=(b.expected(b.CORE)<=keys) or d["extended_complete"]!=(b.expected(b.LEADS)<=keys):
        raise ValueError("Invalid coverage claim")
    if d["core_rows"]!=len(keys&b.expected(b.CORE)) or d["extended_rows"]!=len(keys&b.expected((336,))):
        raise ValueError("Invalid feature counts")
    return d

def load_all(m,validate=False):
    result={}
    for p in (OUT/"shards").glob("*/*.json.gz"):
        d=b.read(p)
        if d["fingerprint"]!=m["fingerprint"] or p!=shard_path(d["date"]):
            raise ValueError("Invalid archive shard "+str(p))
        if validate:check_shard(d,m)
        result[d["date"]]=d
    return result

def plan():
    m=manifest(); dates=b.calendar(); shards=load_all(m)
    new=[d for d in dates if d not in shards]
    retries=[d for d in dates if d in shards and not shards[d]["core_complete"] and shards[d]["attempts"]<b.MAX_ATTEMPTS]
    cap=WORKERS*DATES_PER_WORKER
    # Reserve bounded retry slots; otherwise favor first-pass coverage.
    queue=(retries[:WORKERS]+new+retries[WORKERS:])[:cap]
    jobs=[dict(worker=i,dates=",".join(queue[i::WORKERS])) for i in range(WORKERS) if queue[i::WORKERS]]
    outputs={"matrix":json.dumps({"include":jobs},separators=(",",":")),"has_work":"true" if jobs else "false","planned":str(len(queue))}
    with open(os.environ["GITHUB_OUTPUT"],"a") as f:
        for k,v in outputs.items():f.write(k+"="+v+"\n")
    pathlib.Path("batch-plan.json").write_text(json.dumps(dict(dates=queue,workers=jobs,fingerprint=m["fingerprint"]),indent=2))
    print("Planned",len(queue),"dates in",len(jobs),"workers",flush=True)

def worker():
    m=manifest(); dates=os.environ["BATCH_DATES"].split(","); allowed=set(b.calendar())
    if not dates or len(dates)>DATES_PER_WORKER or len(set(dates))!=len(dates) or not set(dates)<=allowed:
        raise ValueError("Invalid work allocation")
    points=m["points"]
    b.source.POINTS=[(p["lat"],p["lon"]) for p in points]
    dest=pathlib.Path("batch-output");dest.mkdir(exist_ok=True)
    started=time.monotonic()
    count=0
    for date in dates:
        if time.monotonic()-started>BUDGET_SECONDS:break
        p=shard_path(date);old=b.read(p) if p.exists() else {}
        if old and (old["core_complete"] or old["attempts"]>=b.MAX_ATTEMPTS):continue
        d=b.build(date,old,points);d["fingerprint"]=m["fingerprint"]
        check_shard(d,m);b.save(dest/(date+".json.gz"),d);count+=1
        print(date,"core",d["core_rows"],"extended",d["extended_rows"],"elapsed seconds",round(time.monotonic()-started),flush=True)
    (dest/"worker.json").write_text(json.dumps(dict(worker=os.environ["BATCH_WORKER"],assigned=len(dates),processed=count,elapsed_seconds=round(time.monotonic()-started),fingerprint=m["fingerprint"])))

def publish():
    m=manifest(); dates=b.calendar(); before=load_all(m)
    plan=json.loads(pathlib.Path("batch-plan.json").read_text())
    if plan["fingerprint"]!=m["fingerprint"]:raise ValueError("Plan manifest mismatch")
    assigned=set(plan["dates"])
    if not assigned<=set(dates):raise ValueError("Unknown planned date")
    received={};timings=[]
    for p in pathlib.Path("worker-artifacts").rglob("worker.json"):
        t=json.loads(p.read_text())
        if t["fingerprint"]==m["fingerprint"]:timings.append(t)
    for p in pathlib.Path("worker-artifacts").rglob("*.json.gz"):
        d=check_shard(b.read(p),m);date=d["date"]
        if date not in assigned or date in received or p.name!=date+".json.gz":raise ValueError("Unexpected or duplicate worker result")
        received[date]=d
    if not received:raise RuntimeError("No worker checkpoints recovered; previous archive preserved")
    # Prevent regressing successful data or replacing newer retry attempts.
    for date,d in received.items():
        old=before.get(date)
        if old:
            if old["attempts"]>=d["attempts"]:continue
            prev={b.key(r):r for r in old["rows"]}; curr={b.key(r):r for r in d["rows"]}
            if not prev.keys()<=curr.keys():raise ValueError("Worker dropped existing features")
        b.save(shard_path(date),d)
    shards=load_all(m)
    coverage=collections.Counter(d[:4] for d in dates)
    core=collections.Counter(d[:4] for d,v in shards.items() if v["core_complete"])
    attempted=collections.Counter(d[:4] for d in shards)
    errors=collections.Counter("not_found" if "404" in e.get("error","") else "other" for v in shards.values() for e in v["errors"])
    report=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        total_initializations=len(dates),attempted_initializations=len(shards),
        core_complete=sum(v["core_complete"] for v in shards.values()),
        extended_complete=sum(v["extended_complete"] for v in shards.values()),
        feature_rows=sum(len(v["rows"]) for v in shards.values()),point_count=len(m["points"]),
        member_count=len(b.MEMBERS),max_dates_per_run=WORKERS*DATES_PER_WORKER,
        workers=WORKERS,planned_this_run=len(assigned),received_this_run=len(received),
        new_this_run=len(set(shards)-set(before)),worker_timings=timings,
        core_leads=b.CORE,extended_lead=336,errors=dict(errors),
        exhausted_core=sum(not v["core_complete"] and v["attempts"]>=b.MAX_ATTEMPTS for v in shards.values()),
        by_year=[dict(year=y,total=coverage[y],attempted=attempted[y],core_complete=core[y]) for y in sorted(coverage)],
        training_ready=False,training_blockers=["Observation windows must be verified and aligned before joining labels.","Independent winter-season validation and adequate event counts are required."],
        note="Parallel checkpoint compilation. Missing extended forecasts are not zeros. Core retries limited to three attempts.")
    b.atomic_json(OUT/"progress.json",report);print(json.dumps(report),flush=True)

if __name__=="__main__":
    {"plan":plan,"worker":worker,"publish":publish}[sys.argv[1]]()
