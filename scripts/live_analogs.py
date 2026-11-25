"""Live atmospheric analogs, with strictly earlier archived cases and reported-date outcomes.
No calibrated probabilities or operational storm totals."""
import csv
import datetime as dt
import gzip
import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import regional_live_v2 as v2
import regional_live as live
import regional_learning as learn

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/live_analogs"
def day(s):return dt.datetime.strptime(s,"%Y%m%d").date()
def seasonal(a,b):
    delta=abs((dt.date(2000,a.month,a.day)-dt.date(2000,b.month,b.day)).days)
    return min(delta,366-delta)
def array(shard,lead,n):
    lookup={(r["member"],r["lead_hours"],r["field"]):r["values"] for r in shard["rows"]}
    x=np.array([[lookup[m,lead,f] for m in live.MEMBERS] for f in live.FIELDS],float)
    if x.shape!=(6,5,n) or not np.isfinite(x).all():raise ValueError("Bad archive array")
    return x
def signature(a,i):
    mean=a.mean(axis=1);sd=a.std(axis=1)
    local=np.concatenate((mean[:,i],sd[:,i]))
    syn=np.concatenate((mean[:,:10].ravel(),sd[:,:10].ravel()))
    return np.concatenate((local,syn))
def miles(a,b):
    p,q=map(math.radians,(a["lat"],b["lat"]))
    d=math.radians(a["lon"]-b["lon"])
    return 3958.8*2*math.asin(min(1,math.sqrt(math.sin((p-q)/2)**2+math.cos(p)*math.cos(q)*math.sin(d/2)**2)))
def rank(q,dates,x):
    # Equal weight for local and circulation blocks, standardized on candidates only.
    scale=x.std(axis=0);valid=scale>1e-8
    z=np.zeros_like(x)
    z[:,valid]=(x[:,valid]-q[valid])/scale[valid]
    loc=valid[:12];syn=valid[12:]
    if not loc.any() or not syn.any():raise ValueError("No variable features")
    distance=np.sqrt(.5*np.mean(z[:,:12][:,loc]**2,axis=1)+.5*np.mean(z[:,12:][:,syn]**2,axis=1))
    order=np.argsort(distance);chosen=[]
    for i in order:
        if any(abs((dates[i]-dates[j]).days)<7 for j in chosen):continue
        chosen.append(int(i))
        if len(chosen)==10:break
    return [(dates[i],float(distance[i])) for i in chosen]
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((live.ARCH/"manifest.json").read_text())
    live.historical_units(manifest)
    points=[dict(p,kind="synoptic" if i<10 else "station") for i,p in enumerate(manifest["points"])]
    points.extend(dict(label=p["name"],lat=p["lat"],lon=p["lon"],kind="town") for p in v2.storm.POINTS if p["kind"]=="town")
    coords=[(p["lat"],p["lon"]) for p in points];n=len(manifest["points"])
    obs={}
    with gzip.open(ROOT/"data/regional/observations.csv.gz","rt",newline="") as f:
        for r in csv.DictReader(f):obs[r["station"],r["reported_date"]]=float(r["snow_mm"])/25.4
    meta={s["id"]:s["name"] for s in json.loads((ROOT/"data/regional/stations.json").read_text())}
    results=[];errors=[]
    for stamp,lead in v2.WINDOWS:
        arch=live.ARCH if lead==24 else ROOT/"data/gefs48"
        qdate=day(stamp);dates=[];history=[]
        for p in sorted(arch.glob("shards/*/*.json.gz")):
            with gzip.open(p,"rt") as f:s=json.load(f)
            d=day(s["date"])
            if not s["core_complete"] or s["fingerprint"]!=manifest["fingerprint"] or learn.season(d)>=learn.season(qdate) or seasonal(d,qdate)>45:continue
            dates.append(d);history.append(array(s,lead,n))
        result=dict(initialization=stamp+"T00Z",lead_hours=lead,candidates=len(dates),towns=[],status="insufficient_candidates")
        if len(dates)<30:results.append(result);continue
        members={}
        with ThreadPoolExecutor(max_workers=5) as ex:
            for m,f in [(m,ex.submit(live.get_member,stamp,lead,m,coords)) for m in live.MEMBERS]:
                try:members[m]=f.result()
                except Exception as e:errors.append(dict(lead_hours=lead,member=m,error=str(e)))
        if len(members)!=5:result["status"]="incomplete_live_inputs";results.append(result);continue
        q=np.stack([members[m] for m in live.MEMBERS],axis=1)
        result["field_resolution_degrees"]={f:sorted({r for (d,m,l,v,lev),r in v2.USED.items() if d==stamp and l==lead and v==var and lev==level}) for f,(var,level) in live.VARS.items()}
        result["field_resolution_degrees"]["precip24"]=[.25]
        for i,p in enumerate(points[n:],n):
            j=min(range(10,n),key=lambda k:miles(p,points[k]));station=points[j]
            x=np.stack([signature(a,j) for a in history])
            chosen=rank(signature(q,i),dates,x)
            matches=[]
            for d,distance in chosen:
                outcomes={}
                for mapping,offset in learn.MAPPINGS.items():
                    label=(d+dt.timedelta(hours=lead)+dt.timedelta(days=offset)).isoformat()
                    outcomes[mapping]=dict(reported_date=label,snow_inches=obs.get((station["label"],label)))
                matches.append(dict(initialization=d.isoformat(),distance=round(distance,4),outcomes=outcomes))
            result["towns"].append(dict(town=p["label"],station=station["label"],station_name=meta.get(station["label"]),station_distance_miles=round(miles(p,station),1),matches=matches,
                coverage={m:sum(r["outcomes"][m]["snow_inches"] is not None for r in matches) for m in learn.MAPPINGS}))
        result["status"]="research_matches";results.append(result)
    report=dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),results=results,errors=errors,operational_ready=False,
        note="Nearest historical atmospheric patterns from earlier seasons, +/-45 calendar days; local and synoptic blocks equally weighted, 7-day separation. Nearest observation station is a proxy, not town snowfall. Reported-date mappings unverified. Distances are not probabilities. No estimate or outcome-based selection; no validated predictive skill yet.")
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps(report,allow_nan=False),flush=True)
if __name__=="__main__":main()
