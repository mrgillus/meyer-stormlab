"""Two-part snowfall benchmark, separate from operational forecasting."""
import datetime as dt
import json
from collections import Counter
from pathlib import Path
import numpy as np
import joblib
from sklearn.ensemble import RandomForestRegressor,RandomForestClassifier
import regional_learning as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/heavy_snow"
def arrays(rows):
    x=np.stack([r[4] for r in rows]);y=np.array([r[3] for r in rows])
    counts=Counter(r[1] for r in rows);w=np.array([1/counts[r[1]] for r in rows])
    return x,y,w/w.mean()
def forest(reg=True):
    cls=RandomForestRegressor if reg else RandomForestClassifier
    return cls(n_estimators=120,max_depth=12,min_samples_leaf=10,max_features=.6,n_jobs=2,random_state=42)
def ptrue(model,x):
    return model.predict_proba(x)[:,list(model.classes_).index(True)]
def event_score(y,alert,w,t):
    actual=y>=t;alert=np.asarray(alert,bool)
    h=int(np.sum(actual&alert));m=int(np.sum(actual&~alert));f=int(np.sum(~actual&alert))
    wh=float(w[actual&alert].sum());wm=float(w[actual&~alert].sum());wf=float(w[~actual&alert].sum())
    return dict(hits=h,misses=m,false_alarms=f,observed=int(actual.sum()),pod=h/(h+m) if h+m else None,
        far=f/(h+f) if h+f else None,csi=h/(h+m+f) if h+m+f else None,
        date_weighted_csi=wh/(wh+wm+wf) if wh+wm+wf else None)
def event_groups(rows,t):
    days=sorted({dt.date.fromisoformat(r[1]) for r in rows if r[3]>=t})
    return sum(i==0 or (d-days[i-1]).days>2 for i,d in enumerate(days))
def fit(rows,lead,mapping):
    groups={g:[r for r in rows if r[0]==g] for g in ("train","validation","test")}
    sizes={g:base.stats(rr) for g,rr in groups.items()}
    out=dict(lead_hours=lead,label_mapping=mapping,coverage=sizes,status="insufficient_data")
    if any(sizes[g]["dates"]<60 or sizes[g]["regional_event_groups"]<5 for g in groups):return out
    x,y,w=arrays(groups["train"]);v,vy,vw=arrays(groups["validation"]);z,zy,zw=arrays(groups["test"])
    snow=y>=.1
    if snow.sum()<60 or (~snow).sum()<60:return out
    occurrence=forest(False);occurrence.fit(x,snow,sample_weight=w)
    amount=forest();amount.fit(x[snow],y[snow],sample_weight=w[snow])
    baseline=forest();baseline.fit(x,y,sample_weight=w)
    alerts={};scores={};models={}
    for t in (1.,3.,6.):
        coverage={g:dict(station_days=sum(r[3]>=t for r in rr),event_groups=event_groups(rr,t)) for g,rr in groups.items()}
        detail=dict(threshold_inches=t,coverage=coverage,status="insufficient_events")
        if coverage["train"]["event_groups"]<5 or coverage["validation"]["event_groups"]<5 or coverage["test"]["event_groups"]<3:
            alerts[str(t)]=detail;continue
        clf=forest(False);clf.fit(x,y>=t,sample_weight=w)
        vp=ptrue(clf,v);zp=ptrue(clf,z)
        cutoff=max(np.arange(.05,.801,.05),key=lambda c:(event_score(vy,vp>=c,vw,t)["date_weighted_csi"] or 0,c))
        models[str(t)]=clf
        detail.update(status="evaluated_research",validation_selected_cutoff=float(cutoff),
            validation=event_score(vy,vp>=cutoff,vw,t),test=event_score(zy,zp>=cutoff,zw,t),
            baseline_test=event_score(zy,baseline.predict(z)>=t,zw,t),
            test_brier=float(np.average((zp-(zy>=t))**2,weights=zw)),probabilities_calibrated=False)
        alerts[str(t)]=detail
    for group,xx,yy,ww in (("validation",v,vy,vw),("test",z,zy,zw)):
        rr=groups[group];p=ptrue(occurrence,xx);conditional=np.maximum(0,amount.predict(xx));hurdle=p*conditional;rf=baseline.predict(xx)
        scores[group]={name:base.score(rr,vals) for name,vals in (("hurdle_expected",hurdle),("RF",rf),("cold_precip_10to1",[r[5] for r in rr]),("no_snow",np.zeros(len(rr))))}
        for name,vals in (("hurdle_expected",hurdle),("RF",rf)):
            heavy=yy>=3
            scores[group][name]["heavy_snow_mae"]=float(np.average(np.abs(vals[heavy]-yy[heavy]),weights=ww[heavy])) if heavy.any() else None
            scores[group][name]["heavy_snow_bias"]=float(np.average(vals[heavy]-yy[heavy],weights=ww[heavy])) if heavy.any() else None
    out.update(status="evaluated_research",metrics=scores,alerts=alerts)
    joblib.dump(dict(occurrence=occurrence,conditional_amount=amount,baseline=baseline,alerts=models,
        thresholds={t:d["validation_selected_cutoff"] for t,d in alerts.items() if d["status"]=="evaluated_research"},
        lead_hours=lead,label_mapping=mapping,feature_version=1,fields=base.FIELDS,members=base.MEMBERS),OUT/f"research_{lead}_{mapping}.joblib",compress=3)
    return out
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    sources=[];results=[]
    for arch,leads in ((ROOT/"data/gefs_archive",(24,72,168)),(ROOT/"data/gefs48",(48,))):
        if not (arch/"manifest.json").exists():continue
        base.ARCH=arch;base.LEADS=leads
        manifest,batches,n,digest,bad=base.load()
        sources.append(dict(archive=arch.name,complete_shards=n,fingerprint=manifest["fingerprint"],snapshot_sha256=digest,feature_errors=bad))
        for (lead,mapping),rr in sorted(batches.items()):
            print("Benchmark",lead,mapping,len(rr),flush=True)
            r=fit(rr,lead,mapping);results.append(r);print(json.dumps(r,allow_nan=False),flush=True)
    report=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),sources=sources,results=results,
        operational_ready=False,method="Two-part 0.1-inch occurrence and conditional amount model; 1, 3 and 6 inch threshold classifiers with validation-only CSI cutoff selection. Equal aggregate weight per date; winter-season holdouts.",
        limitations=["Reported-date mapping unresolved; test winters have already been examined in earlier research and are not a pristine final deployment holdout.","Probabilities uncalibrated; station-days and approximate event groups are correlated.","Live mixed-grid compatibility not validated; no operational promotion."])
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
if __name__=="__main__":main()
