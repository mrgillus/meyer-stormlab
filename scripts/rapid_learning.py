"""Short-range snowfall learning experiment, separate from production guidance.
Fixed hourly offsets, matched local days, chronological holdout, no live-run substitution.
"""
import csv
import datetime as dt
import json
import math
import statistics as st
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hindcast as h

OUT=h.ROOT/"data"/"rapid_learning"
MODELS={**h.MODELS,"HRRR":"ncep_hrrr_conus","RRFS":"ncep_rrfs_conus","NBM":"ncep_nbm_conus"}
NEW=("HRRR","RRFS","NBM")
LEADS=(1,2)
TEST=h.TEST_START
STORM=("2026-11-25","2026-11-26")

def fetch(model,start,end):
    a=(dt.date.fromisoformat(start)-dt.timedelta(days=1)).isoformat()
    b=(dt.date.fromisoformat(end)+dt.timedelta(days=1)).isoformat()
    keys={l:f"snowfall_previous_day{l}" for l in LEADS}
    params=dict(latitude=h.LAT,longitude=h.LON,models=model,hourly=",".join(keys.values()),
                start_date=a,end_date=b,timezone="GMT")
    raw=json.loads(h.get("https://previous-runs-api.open-meteo.com/v1/forecast?"+urllib.parse.urlencode(params)))
    hour=raw["hourly"];units=raw["hourly_units"]
    bucket=defaultdict(list)
    for i,t in enumerate(hour["time"]):
        ts=dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        day=(ts-dt.timedelta(microseconds=1)).astimezone(h.TZ).date().isoformat()
        if start<=day<=end:bucket[day].append((ts,i))
    result={}
    for day,ix in bucket.items():
        d=dt.date.fromisoformat(day)
        start_utc=dt.datetime.combine(d,dt.time(),h.TZ).astimezone(dt.timezone.utc)
        end_utc=dt.datetime.combine(d+dt.timedelta(days=1),dt.time(),h.TZ).astimezone(dt.timezone.utc)
        expected={start_utc+dt.timedelta(hours=j) for j in range(1,int((end_utc-start_utc).total_seconds()/3600)+1)}
        if len(ix)!=len(expected) or {t for t,i in ix}!=expected:continue
        for l,k in keys.items():
            col=hour.get(k,[]);unit=units.get(k)
            if len(col)!=len(hour["time"]) or unit not in ("cm","mm","inch","in"):continue
            vals=[col[i] for t,i in ix]
            if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>=-0.001 for v in vals):continue
            result[(day,l)]=sum(max(0,v) for v in vals)/{"cm":2.54,"mm":25.4,"inch":1,"in":1}[unit]
    return result

def gather(periods):
    result={n:{} for n in MODELS};errors=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs=[(n,a,b,pool.submit(fetch,m,a,b)) for n,m in MODELS.items() for a,b in periods]
        for n,a,b,f in jobs:
            try:
                data=f.result();result[n].update(data)
                print(n,a,b,"complete lead-days",len(data),flush=True)
            except Exception as e:errors.append(dict(model=n,start=a,end=b,error=str(e)))
    return result,errors

def metrics(y,p):return h.scores(list(y),list(p))

def evaluate(rows,lead,names,label,base):
    import numpy as np
    from scipy.optimize import minimize
    train=[r for r in rows if r["date"]<TEST and all(n in r["models"] for n in names)]
    test=[r for r in rows if r["date"]>=TEST and all(n in r["models"] for n in names)]
    ntrain=sum(r["actual"]>=1 for r in train);ntest=sum(r["actual"]>=1 for r in test)
    out=dict(lead=lead,label=label,models=names,train_days=len(train),test_days=len(test),
             train_snow_days=ntrain,test_snow_days=ntest,status="insufficient_data")
    if len(names)<2 or len(train)<90 or len(test)<60 or ntrain<5 or ntest<3:return out
    X=np.array([[r["models"][n] for n in names] for r in train]);Y=np.array([r["actual"] for r in train])
    prior=np.ones(len(names))/len(names)
    # Prespecified shrinkage: learn a convex blend, no extrapolating multiplicative scaling.
    def loss(w):return float(np.sum((X@w-Y)**2)+4*np.sum((w-prior)**2))
    opt=minimize(loss,prior,bounds=[(0.,1.)]*len(names),constraints=[{"type":"eq","fun":lambda w:float(w.sum()-1)}],method="SLSQP",options={"maxiter":300,"ftol":1e-10})
    if not opt.success or not np.all(np.isfinite(opt.x)) or abs(opt.x.sum()-1)>1e-6:return dict(out,status="fit_failed")
    w=np.clip(opt.x,0,1);w/=w.sum()
    Z=np.array([[r["models"][n] for n in names] for r in test]);y=np.array([r["actual"] for r in test])
    comparisons={"learned":Z@w,"equal_all":Z.mean(axis=1)}
    base_cols=[names.index(n) for n in base if n in names]
    if base_cols:comparisons["equal_global"]=Z[:,base_cols].mean(axis=1)
    for i,n in enumerate(names):comparisons[n]=Z[:,i]
    out.update(status="evaluated",weights={n:round(float(v),8) for n,v in zip(names,w)},
        train_max={n:round(float(X[:,i].max()),5) for i,n in enumerate(names)},
        metrics={k:metrics(y,p) for k,p in comparisons.items()},
        test_events=[dict(date=r["date"],actual=r["actual"],predictions={k:round(float(v[i]),3) for k,v in comparisons.items()})
            for i,r in enumerate(test) if r["actual"]>=1 or any(v[i]>=1 for v in comparisons.values())])
    return out

def train():
    obs=h.observations()
    dates=sorted(d for d in obs if any(a<=d<=b for a,b in h.PERIODS))
    forecasts,errors=gather(h.PERIODS)
    coverage=[];results=[];all_rows=[]
    for l in LEADS:
        rows=[dict(date=d,actual=obs[d],models={n:v[(d,l)] for n,v in forecasts.items() if (d,l) in v}) for d in dates]
        all_rows.extend(dict(r,lead=l) for r in rows)
        tr=[r for r in rows if r["date"]<TEST];te=[r for r in rows if r["date"]>=TEST]
        c={n:dict(train_days=sum(n in r["models"] for r in tr),test_days=sum(n in r["models"] for r in te),
                  train_snow_days=sum(n in r["models"] and r["actual"]>=1 for r in tr),
                  test_snow_days=sum(n in r["models"] and r["actual"]>=1 for r in te)) for n in MODELS}
        coverage.append(dict(lead=l,train_observed_days=len(tr),test_observed_days=len(te),models=c))
        # Coverage selection only uses training. Test coverage is an evaluation gate.
        base=[n for n in h.MODELS if c[n]["train_days"]>=.8*len(tr)]
        if len(base)<2:base=[n for n in h.MODELS if c[n]["train_days"]>=90 and c[n]["train_snow_days"]>=5]
        results.append(evaluate(rows,l,base,"global_baseline",base))
        for n in NEW:results.append(evaluate(rows,l,base+[n],"add_"+n,base))
        eligible=[n for n in NEW if c[n]["train_days"]>=90 and c[n]["train_snow_days"]>=5]
        if eligible:results.append(evaluate(rows,l,base+eligible,"expanded",base))
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/"matched.csv").open("w",newline="") as f:
        w=csv.writer(f);w.writerow(["date","lead_offset_days","observed_snow_in",*MODELS])
        for r in all_rows:w.writerow([r["date"],r["lead"],r["actual"],*[r["models"].get(n,"") for n in MODELS]])
    report=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),station=h.STATION,
        training_period="Jan–Mar 2024 and Oct 2024–Mar 2025",holdout_period="Oct 2025–Mar 2026",
        coverage=coverage,results=results,errors=errors,method="Nonnegative sum-to-one ridge-shrunk snowfall blend; exact common cases, chronological holdout; all candidates reported, no winner chosen on holdout.",
        limitations=["Research only; observation reporting windows remain unresolved.","Fixed hourly offsets are not a single model issuance or latest stitched forecast.","Limited snow events, changing model versions, possible NBM overlap with individual models.","RRFS may lack older winter archives. Missing historical forecasts are never replaced with zeros or another model.","No learned regional forecasts or calibrated probabilities."])
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps(dict(coverage=coverage,results=[{k:v for k,v in r.items() if k!="test_events"} for r in results],errors=errors),indent=2),flush=True)

def live():
    report=json.loads((OUT/"report.json").read_text())
    now=dt.datetime.now(dt.timezone.utc)
    if now>=dt.datetime(2026,11,27,0,tzinfo=h.TZ).astimezone(dt.timezone.utc):
        print("Storm window ended; preserving prior inference");return
    values,errors=gather([(STORM[0],STORM[-1])])
    predictions=[]
    for r in report["results"]:
        if r["status"]!="evaluated":continue
        l=r["lead"];names=r["models"]
        for day in STORM:
            end=dt.datetime.combine(dt.date.fromisoformat(day)+dt.timedelta(days=1),dt.time(),h.TZ).astimezone(dt.timezone.utc)
            item=dict(date=day,lead=l,label=r["label"],models=names)
            if end-dt.timedelta(days=l)>now:
                predictions.append(dict(item,status="not_yet_eligible"));continue
            raw={n:values[n].get((day,l)) for n in names}
            if any(v is None for v in raw.values()):
                predictions.append(dict(item,status="missing_fixed_offset_inputs",raw=raw));continue
            outside=[n for n,v in raw.items() if v>r["train_max"][n]+1e-4]
            p=sum(r["weights"][n]*v for n,v in raw.items())
            predictions.append(dict(item,status="outside_training_range" if outside else "experimental",
                raw=raw,exceeds_training_range=outside,learned_inches=None if outside else round(p,3),
                equal_inches=round(st.mean(raw.values()),3)))
    payload=dict(generated_at=now.isoformat(),training_generated_at=report["generated_at"],station=h.STATION,
        predictions=predictions,errors=errors,note="Fixed-offset shadow inference at Lambert only. No newest-run substitution. Out-of-training-range learned outputs withheld; no operational promotion.")
    (OUT/"live.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
    print(json.dumps(payload,indent=2),flush=True)
if __name__=="__main__":
    import sys
    if sys.argv[1]=="train":train()
    elif sys.argv[1]=="live":live()
    else:raise SystemExit("Expected train or live")
