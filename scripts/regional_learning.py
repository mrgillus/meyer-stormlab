"""Regional GEFS snowfall research benchmark. Reported-date labels, not aligned UTC snowfall.
Two prespecified date mappings are reported independently. No operational promotion.
"""
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier, HistGradientBoostingRegressor
import joblib

ROOT=Path(__file__).resolve().parents[1]
ARCH=ROOT/"data/gefs_archive"
OUT=ROOT/"data/regional_learning"
FIELDS=("t2m","t850","z500","mslp","pwat","precip24")
LEADS=(24,72,168)
MEMBERS=("c00","p01","p02","p03","p04")
MAPPINGS={"utc_end_date":0,"utc_end_previous_date":-1}
THRESHOLD=1.
TRAIN=range(2000,2013)
VALID=range(2013,2016)
TEST=range(2016,2019)

def season(day):return day.year if day.month>=9 else day.year-1
def split(day):
    y=season(day)
    return "train" if y in TRAIN else "validation" if y in VALID else "test" if y in TEST else None

def load():
    manifest=json.loads((ARCH/"manifest.json").read_text())
    if tuple(manifest["members"])!=MEMBERS or tuple(manifest["fields"])!=FIELDS:
        raise ValueError("Unexpected archive feature schema")
    points=manifest["points"]
    obs={}
    with gzip.open(ROOT/"data/regional/observations.csv.gz","rt",newline="") as f:
        for r in csv.DictReader(f):
            key=(r["station"],r["reported_date"])
            val=float(r["snow_mm"])/25.4
            if not math.isfinite(val) or val<0 or key in obs:raise ValueError("Bad or duplicate observation")
            obs[key]=val
    batches=defaultdict(list);bad=[];shards=0; digest=hashlib.sha256()
    for path in sorted((ARCH/"shards").glob("*/*.json.gz")):
        with gzip.open(path,"rt") as f:s=json.load(f)
        if s.get("fingerprint")!=manifest["fingerprint"]:raise ValueError("Mixed archive fingerprints")
        if not s["core_complete"]:continue
        init=dt.datetime.strptime(s["date"],"%Y%m%d").date()
        digest.update(path.read_bytes())
        lookup={(r["member"],r["lead_hours"],r["field"]):r for r in s["rows"]}
        if len(lookup)!=len(s["rows"]):raise ValueError("Duplicate shard features")
        shards+=1
        for lead in LEADS:
            try:
                data=np.array([[lookup[(m,lead,f)]["values"] for m in MEMBERS] for f in FIELDS],dtype=float)
                if data.shape!=(len(FIELDS),len(MEMBERS),len(points)) or not np.isfinite(data).all():raise ValueError("Feature shape or invalid value")
                if (data[5]<-.001).any():raise ValueError("Negative precipitation")
                if not (200<data[0]).all() or not (data[0]<350).all():raise ValueError("Unexpected temperature units")
            except (KeyError,ValueError) as e:
                bad.append(dict(init=s["date"],lead=lead,error=str(e)));continue
            mean=data.mean(axis=1);sd=data.std(axis=1)
            # Regional circulation + ensemble uncertainty, local grid values and seasonal/location features.
            syn=np.concatenate((mean[:,:10].ravel(),sd[:,:10].ravel()))
            end=init+dt.timedelta(hours=lead)
            for label,offset in MAPPINGS.items():
                target=end+dt.timedelta(days=offset)
                group=split(target)
                if group is None or split(init)!=group:continue
                doy=target.timetuple().tm_yday
                for i,p in enumerate(points[10:],10):
                    value=obs.get((p["label"],target.isoformat()))
                    if value is None:continue
                    local=np.concatenate((mean[:,i],sd[:,i],
                        [p["lat"],p["lon"],math.sin(doy*2*math.pi/365.25),math.cos(doy*2*math.pi/365.25),
                         float(np.mean(data[0,:,i]<=273.15)),float(np.mean(data[1,:,i]<=273.15))]))
                    x=np.concatenate((local,syn))
                    # Physical reference: 10:1 liquid-to-snow if both sampled temperatures cold.
                    ref=float(np.mean(np.maximum(data[5,:,i],0)*10/25.4*
                        ((data[0,:,i]<=274.15)&(data[1,:,i]<=273.15))))
                    batches[(lead,label)].append((group,target.isoformat(),p["label"],value,x,ref))
    return manifest,batches,shards,digest.hexdigest(),bad

def stats(rows):
    dates=sorted({r[1] for r in rows});snow=sorted({r[1] for r in rows if r[3]>=THRESHOLD})
    events=0;previous=None
    for date in snow:
        now=dt.date.fromisoformat(date)
        if previous is None or (now-previous).days>2:events+=1
        previous=now
    return dict(station_days=len(rows),dates=len(dates),stations=len({r[2] for r in rows}),
        snow_station_days=sum(r[3]>=THRESHOLD for r in rows),snow_dates=len(snow),regional_event_groups=events)

def score(rows,p,prob=None):
    y=np.array([r[3] for r in rows]);p=np.maximum(0,np.asarray(p,float));err=p-y
    counts=Counter(r[1] for r in rows)
    w=np.array([1/counts[r[1]] for r in rows]);w/=w.sum()
    snow=y>=THRESHOLD
    out=dict(date_weighted_mae=float(np.sum(w*np.abs(err))),date_weighted_rmse=float(np.sqrt(np.sum(w*err**2))),
        bias=float(np.sum(w*err)),snow_day_mae=float(np.average(np.abs(err[snow]),weights=w[snow])) if snow.any() else None,
        hits=int(np.sum(snow&(p>=THRESHOLD))),misses=int(np.sum(snow&(p<THRESHOLD))),
        false_alarms=int(np.sum(~snow&(p>=THRESHOLD))),n=len(y))
    if prob is not None:out["brier_1in"]=float(np.sum(w*(np.asarray(prob)-snow)**2))
    return out

def evaluate(lead,mapping,rows,outrows):
    groups={g:[r for r in rows if r[0]==g] for g in ("train","validation","test")}
    sizes={g:stats(v) for g,v in groups.items()}
    result=dict(lead_hours=lead,label_mapping=mapping,coverage=sizes,status="insufficient_data")
    if any(sizes[g]["dates"]<60 or sizes[g]["regional_event_groups"]<5 for g in groups):return result
    tr=groups["train"];X=np.stack([r[4] for r in tr]);y=np.array([r[3] for r in tr])
    counts=Counter(r[1] for r in tr);weights=np.array([1/counts[r[1]] for r in tr])
    weights*=len(weights)/weights.sum()
    # Fixed hyperparameters. Validation is diagnostic, test winters not used for fitting or selection.
    rf=RandomForestRegressor(n_estimators=120,max_depth=12,min_samples_leaf=10,max_features=.6,n_jobs=2,random_state=42)
    rf.fit(X,y,sample_weight=weights)
    clf=RandomForestClassifier(n_estimators=100,max_depth=12,min_samples_leaf=12,max_features=.6,n_jobs=2,random_state=42)
    clf.fit(X,y>=THRESHOLD,sample_weight=weights)
    hgb=HistGradientBoostingRegressor(loss="poisson",max_iter=150,max_leaf_nodes=15,min_samples_leaf=25,l2_regularization=3,random_state=42,early_stopping=False)
    hgb.fit(X,y,sample_weight=weights)
    climo={};occ={}
    for station in {r[2] for r in tr}:
        v=np.array([r[3] for r in tr if r[2]==station]);climo[station]=float(v.mean());occ[station]=float(np.mean(v>=THRESHOLD))
    overall=float(np.average(y,weights=weights));overall_occ=float(np.average(y>=THRESHOLD,weights=weights))
    result.update(status="evaluated_research",metrics={})
    for group in ("validation","test"):
        rr=groups[group];xx=np.stack([r[4] for r in rr]);pred=rf.predict(xx);p=clf.predict_proba(xx)[:,list(clf.classes_).index(True)]
        boost=hgb.predict(xx);base=[climo.get(r[2],overall) for r in rr];bp=[occ.get(r[2],overall_occ) for r in rr]
        comparisons={"RF":score(rr,pred,p),"boosted":score(rr,boost),"cold_precip_10to1":score(rr,[r[5] for r in rr]),
                     "station_climatology":score(rr,base,bp),"no_snow":score(rr,np.zeros(len(rr)))}
        result["metrics"][group]=comparisons
        if group=="test":
            for i,r in enumerate(rr):outrows.append([lead,mapping,r[1],r[2],r[3],float(pred[i]),float(boost[i]),float(p[i]),r[5]])
    joblib.dump(dict(rf=rf,boosted=hgb,classifier=clf,lead_hours=lead,label_mapping=mapping,
        feature_version=1,fields=FIELDS,members=MEMBERS,train_seasons=list(TRAIN),
        training_min=X.min(axis=0),training_max=X.max(axis=0)),OUT/f"research_{lead}_{mapping}.joblib",compress=3)
    return result

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest,batches,n,digest,bad=load();results=[];predictions=[]
    for (lead,mapping),rows in sorted(batches.items()):
        print("Training",lead,mapping,len(rows),flush=True)
        result=evaluate(lead,mapping,rows,predictions);results.append(result)
        print(json.dumps(result,allow_nan=False),flush=True)
    with gzip.open(OUT/"heldout_predictions.csv.gz","wt",newline="") as f:
        w=csv.writer(f);w.writerow(["lead_hours","reported_date_mapping","reported_date","station","actual_in","rf_in","boosted_in","rf_uncalibrated_p_ge1","cold_precip_in"]);w.writerows(predictions)
    report=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),archive_fingerprint=manifest["fingerprint"],
        archive_snapshot_sha256=digest,complete_shards=n,feature_errors=bad,results=results,
        seasons={"train":list(TRAIN),"validation":list(VALID),"test":list(TEST)},
        operational_ready=False,method="Predict GHCN station-reported date snowfall from GEFS six-field ensemble features. Two prespecified UTC-end date mappings; neither is an asserted verified observation window. Entire winter splits, date-weighted metrics and regional event counts.",
        blockers=["Snowfall-specific reporting clocks and effective dates must be independently verified; mappings are sensitivity experiments.",
                  "Live GEFS feature extraction, units/grid compatibility and model-version shift must be verified before live inference.",
                  "Independent performance and probability calibration must be established before operational promotion."])
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print("Completed",n,"shards",len(results),"experiments",flush=True)
if __name__=="__main__":main()
