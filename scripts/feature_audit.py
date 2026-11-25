"""Audit live feature-domain flags and build a temporal snow-process diagnostic.
Research only; no snowfall forecast is generated from these diagnostics."""
import datetime as dt
import gzip
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import joblib
import regional_live_v2 as v2
import regional_live as live
import regional_learning as learn
import atmospheric_profiles as profile

ROOT=live.ROOT
OUT=ROOT/"data/feature_audit"
def names(points):
    units=["K","K","m","Pa","mm","mm"]
    labels=[]
    for stat in ("mean","std"):
        labels.extend(dict(field=f,stat=stat,location="local",units=u) for f,u in zip(live.FIELDS,units))
    labels.extend(dict(field=f,stat="local",location="local",units=u) for f,u in zip(("latitude","longitude","season_sin","season_cos","cold_t2m_fraction","cold_t850_fraction"),("degrees","degrees","1","1","1","1")))
    for stat in ("mean","std"):
        labels.extend(dict(field=f,stat=stat,location=p["label"],lat=p["lat"],lon=p["lon"],units=u) for f,u in zip(live.FIELDS,units) for p in points[:10])
    assert len(labels)==138
    return labels

def member(date,lead,m,coords):
    return live.get_member(date,lead,m,coords)

def collect(date,lead,coords):
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows=list(pool.map(lambda m:member(date,lead,m,coords),live.MEMBERS))
    return np.stack(rows,axis=1)

def coarse_record(date,member,lead,var,level,step):
    for part in ("a","b"):
        url,rr=live.records(date,member,lead,part)
        matches=[r for r in rr if r[2][3]==var and r[2][4]==level and r[2][5].strip()==step]
        if len(matches)>1:raise ValueError("Ambiguous coarse record")
        if matches:return url,matches[0]
    raise ValueError(f"Missing coarse {var} {level} {step}")

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((live.ARCH/"manifest.json").read_text())
    points=[dict(p,kind="synoptic" if i<10 else "station") for i,p in enumerate(manifest["points"])]
    points.extend(dict(label=p["name"],lat=p["lat"],lon=p["lon"],kind="town") for p in v2.storm.POINTS if p["kind"]=="town")
    coords=[(p["lat"],p["lon"]) for p in points];labels=names(points)
    i=next(i for i,p in enumerate(points) if p["label"]=="Wentzville")
    results=[];errors=[]
    for date,lead in v2.WINDOWS:
        live.record=v2.hi_record
        try:hi=collect(date,lead,coords)
        except Exception as e:errors.append(dict(lead=lead,mode="mixed",error=str(e)));continue
        live.record=coarse_record
        try:lo=collect(date,lead,coords)
        except Exception as e:lo=None;errors.append(dict(lead=lead,mode="coarse",error=str(e)))
        finally:live.record=v2.hi_record
        folder=ROOT/"data"/("regional_learning" if lead==24 else "regional_learning48")
        report=json.loads((folder/"report.json").read_text())
        if report["archive_fingerprint"]!=manifest["fingerprint"]:raise ValueError("Training fingerprint mismatch")
        for mapping,offset in learn.MAPPINGS.items():
            if not any(r["lead_hours"]==lead and r["label_mapping"]==mapping and r["status"]=="evaluated_research" for r in report["results"]):continue
            model=joblib.load(folder/f"research_{lead}_{mapping}.joblib")
            target=dt.datetime.strptime(date,"%Y%m%d").date()+dt.timedelta(days=lead//24+offset)
            x=live.vector(hi,points,i,target);c=live.vector(lo,points,i,target) if lo is not None else None
            low=model["training_min"];high=model["training_max"];tol=np.maximum(1e-6,(high-low)*.001)
            flags=np.flatnonzero((x<low-tol)|(x>high+tol))
            all_flags=[]
            for j in flags:
                width=max(float(high[j]-low[j]),1e-9)
                all_flags.append(dict(index=int(j),**labels[j],training_min=float(low[j]),training_max=float(high[j]),
                    mixed_value=float(x[j]),coarse_value=float(c[j]) if c is not None else None,
                    fraction_of_training_span_outside=float(max(low[j]-x[j],x[j]-high[j],0)/width),
                    coarse_also_outside=bool(c[j]<low[j]-tol[j] or c[j]>high[j]+tol[j]) if c is not None else None))
            results.append(dict(lead_hours=lead,mapping=mapping,training_generated_at=report["generated_at"],flags=all_flags,
                mixed_rf_inches=float(model["rf"].predict(x.reshape(1,-1))[0]),coarse_rf_inches=float(model["rf"].predict(c.reshape(1,-1))[0]) if c is not None else None))
    payload=dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),town="Wentzville",results=results,errors=errors,operational_ready=False,
        note="Named feature audit, identical cycle/members/lead/locations. Mixed vs 0.5-degree sensitivity does not itself prove historical grid equivalence. Out-of-domain features do not justify clipping or operational promotion.")
    (OUT/"report.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
    print(json.dumps(payload,allow_nan=False),flush=True)
if __name__=="__main__":main()
