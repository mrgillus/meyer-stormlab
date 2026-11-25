"""Separate, resumable 48h lead extension. No edits to the existing GEFS archive."""
import datetime as dt
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import gefs_batch as b
import gefs_parallel as original
import regional_archive as src

ROOT=b.ROOT
OUT=ROOT/"data/gefs48"
LEAD=48
CAP=24
WORKERS=3
EXPECTED={(m,LEAD,f) for m in b.MEMBERS for f in b.FIELDS}

def manifest():
    m=original.manifest()
    return m

def path(date):
    return OUT/"shards"/date[:4]/(date+".json.gz")

def validate(s,m):
    if s["fingerprint"]!=m["fingerprint"] or s["schema"]!=1 or s["lead_hours"]!=48 or not 1<=s["attempts"]<=3:
        raise ValueError("Invalid extension provenance")
    keys=set()
    for r in s["rows"]:
        k=b.key(r)
        if k not in EXPECTED or k in keys or r["init"]!=s["date"]+"T00Z":raise ValueError("Unexpected or duplicate feature")
        if len(r["values"])!=len(m["points"]) or not all(src.math.isfinite(v) for v in r["values"]):raise ValueError("Invalid values")
        keys.add(k)
    if s["core_complete"]!=(keys==EXPECTED):raise ValueError("Coverage mismatch")
    return s

def load(m):
    result={}
    for p in OUT.glob("shards/*/*.json.gz"):
        s=validate(b.read(p),m)
        if p!=path(s["date"]):raise ValueError("Shard path mismatch")
        result[s["date"]]=s
    return result

def plan():
    m=manifest();old=load(m)
    # Same outcome-independent year-interleaved order as original archive.
    eligible=[d for d in b.calendar() if original.shard_path(d).exists() and b.read(original.shard_path(d))["core_complete"]]
    retry=[d for d in eligible if d in old and not old[d]["core_complete"] and old[d]["attempts"]<3]
    new=[d for d in eligible if d not in old]
    queue=(retry[:WORKERS]+new+retry[WORKERS:])[:CAP*WORKERS]
    jobs=[dict(worker=i,dates=",".join(queue[i::WORKERS])) for i in range(WORKERS) if queue[i::WORKERS]]
    Path("gefs48-plan.json").write_text(json.dumps(dict(dates=queue,fingerprint=m["fingerprint"])))
    with open(os.environ["GITHUB_OUTPUT"],"a") as f:
        f.write("matrix="+json.dumps({"include":jobs},separators=(",",":"))+"\n")
        f.write("has_work="+("true" if jobs else "false")+"\n")
    print("48h dates planned",len(queue),"eligible",len(eligible),flush=True)

def extract(date,member,points,existing):
    rows=[];errors=[]
    def decode(field,url,rec,end):
        d=src.decode(url,rec,points)
        if d["data_date"]!=int(date) or d["data_time"]!=0 or d["end_step"]!=end:raise ValueError("GRIB provenance mismatch")
        return d
    for name,file,var,level in src.FIELDS:
        if (member,48,name) in existing:continue
        try:
            url=src.file_url(date,member,48,file)
            matches=[r for r in src.index(url) if r[2][3] in ({var,"PRES","MSLET"} if name=="mslp" else {var}) and (level is None or r[2][4]==level) and r[2][5].strip()=="48 hour fcst"]
            if len(matches)!=1:raise ValueError("Missing or ambiguous field")
            rows.append(dict(init=date+"T00Z",member=member,lead_hours=48,field=name,**decode(name,url,matches[0],48)))
        except Exception as e:errors.append(dict(member=member,field=name,error=str(e)))
    if (member,48,"precip24") not in existing:
        try:
            url=src.file_url(date,member,48,"apcp_sfc");idx=src.index(url);blocks=[]
            for end in (30,36,42,48):
                matches=[r for r in idx if r[2][3]=="APCP" and r[2][5].strip()==f"{end-6}-{end} hour acc fcst"]
                if len(matches)!=1:raise ValueError("Missing exact 6h precipitation block")
                d=decode("precip24",url,matches[0],end)
                if d["units"] not in ("kg m**-2","kg m-2","mm") or min(d["values"])<-.001:raise ValueError("Unexpected precipitation")
                blocks.append(d["values"])
            rows.append(dict(init=date+"T00Z",member=member,lead_hours=48,field="precip24",units="mm water equivalent",values=[sum(v) for v in zip(*blocks)]))
        except Exception as e:errors.append(dict(member=member,field="precip24",error=str(e)))
    return rows,errors

def worker():
    m=manifest();points=[(p["lat"],p["lon"]) for p in m["points"]];dates=os.environ["BATCH_DATES"].split(",")
    if len(dates)>CAP or len(set(dates))!=len(dates) or not set(dates)<=set(b.calendar()):raise ValueError("Invalid assignment")
    dest=Path("gefs48-output");dest.mkdir(exist_ok=True);start=time.monotonic()
    for date in dates:
        if time.monotonic()-start>28*60:break
        old=b.read(path(date)) if path(date).exists() else {}
        if old.get("core_complete") or old.get("attempts",0)>=3:continue
        merged={b.key(r):r for r in old.get("rows",[])};errors=[]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures=[ex.submit(extract,date,member,points,merged.copy()) for member in b.MEMBERS]
            for f in futures:
                rows,err=f.result();errors.extend(err)
                for r in rows:merged[b.key(r)]=r
        s=dict(schema=1,date=date,lead_hours=48,fingerprint=m["fingerprint"],attempts=old.get("attempts",0)+1,rows=[merged[k] for k in sorted(merged)],errors=errors,core_complete=set(merged)==EXPECTED)
        b.save(dest/(date+".json.gz"),validate(s,m))
        print(date,len(merged),"/",len(EXPECTED),flush=True)

def publish():
    m=manifest();plan=json.loads(Path("gefs48-plan.json").read_text());old=load(m)
    if plan["fingerprint"]!=m["fingerprint"]:raise ValueError("Plan mismatch")
    received=set()
    for p in Path("gefs48-artifacts").rglob("*.json.gz"):
        s=validate(b.read(p),m);date=s["date"]
        if date not in plan["dates"] or date in received or p.name!=date+".json.gz":raise ValueError("Invalid worker checkpoint")
        received.add(date);prev=old.get(date)
        if prev and prev["attempts"]>=s["attempts"]:continue
        if prev and not {b.key(r) for r in prev["rows"]}<={b.key(r) for r in s["rows"]}:raise ValueError("Lost previous rows")
        b.save(path(date),s)
    if not received:raise ValueError("No checkpoints recovered")
    allrows=load(m)
    b.atomic_json(OUT/"manifest.json",m)
    b.atomic_json(OUT/"progress.json",dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),lead_hours=48,
        complete=sum(s["core_complete"] for s in allrows.values()),attempted=len(allrows),received=len(received),
        total_initializations=len(b.calendar()),feature_rows=sum(len(s["rows"]) for s in allrows.values()),
        exhausted=sum(not s["core_complete"] and s["attempts"]>=3 for s in allrows.values()),fingerprint=m["fingerprint"],
        note="Separate 48h lead, preceding 24h precipitation. Original archive untouched; eligible dates expand as core archive grows."))

if __name__=="__main__":{"plan":plan,"worker":worker,"publish":publish}[sys.argv[1]]()
