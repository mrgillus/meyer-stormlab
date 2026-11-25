"""Compare precipitation-constrained snowfall regressions with the original RF.
Historical endpoint features only; this is not a temporal-profile-trained model.
A separate temporal diagnostic samples the actual live storm in 6-hour blocks.
"""
import datetime as dt
import json
from collections import Counter
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestRegressor
import regional_learning as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/physical_learning"

def x_y_w(rows):
    x=np.stack([r[4] for r in rows]);y=np.array([r[3] for r in rows])
    counts=Counter(r[1] for r in rows);w=np.array([1/counts[r[1]] for r in rows])
    return x,y,w/w.mean()
def forest():
    return RandomForestRegressor(n_estimators=120,max_depth=12,min_samples_leaf=10,max_features=.6,n_jobs=2,random_state=42)
def fit(rows,lead,mapping):
    groups={g:[r for r in rows if r[0]==g] for g in ("train","validation","test")}
    coverage={g:base.stats(rr) for g,rr in groups.items()}
    out=dict(lead_hours=lead,label_mapping=mapping,coverage=coverage,status="insufficient_data")
    if any(c["dates"]<60 or c["regional_event_groups"]<5 for c in coverage.values()):return out
    x,y,w=x_y_w(groups["train"])
    # Learn effective snow/liquid conversion, including rain and zero snowfall.
    # The ratio is an empirical target, NOT a measured snow-to-liquid ratio.
    liquid=np.maximum(x[:,5],0)/25.4
    wet=liquid>=.02
    if wet.sum()<60:return out
    # Bound extreme ratios created by mismatch of daily observation and GEFS timing.
    # Compare the fixed cap with ordinary RF on the identical cases.
    ratio=np.clip(y[wet]/liquid[wet],0,30)
    m=forest();m.fit(x[wet],ratio,sample_weight=w[wet]*np.minimum(liquid[wet],1))
    rf=forest();rf.fit(x,y,sample_weight=w)
    out.update(status="evaluated_research",metrics={})
    for g in ("validation","test"):
        rr=groups[g];xx,yy,ww=x_y_w(rr);liq=np.maximum(xx[:,5],0)/25.4
        preds={"RF":rf.predict(xx),"effective_ratio":m.predict(xx)*liq,"cold_precip_10to1":np.array([r[5] for r in rr]),"no_snow":np.zeros(len(rr))}
        metrics={}
        for name,p in preds.items():
            a=base.score(rr,p);heavy=yy>=3
            a["heavy_mae"]=float(np.average(np.abs(p[heavy]-yy[heavy]),weights=ww[heavy])) if heavy.any() else None
            a["heavy_bias"]=float(np.average(p[heavy]-yy[heavy],weights=ww[heavy])) if heavy.any() else None
            a["heavy_cases"]=int(heavy.sum());metrics[name]=a
        out["metrics"][g]=metrics
    return out
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    sources=[];results=[]
    for arch,leads in ((ROOT/"data/gefs_archive",(24,72,168)),(ROOT/"data/gefs48",(48,))):
        if not (arch/"manifest.json").exists():continue
        base.ARCH=arch;base.LEADS=leads
        manifest,batches,n,digest,bad=base.load()
        sources.append(dict(archive=arch.name,complete_shards=n,snapshot_sha256=digest,fingerprint=manifest["fingerprint"]))
        for (lead,mapping),rows in sorted(batches.items()):
            r=fit(rows,lead,mapping);results.append(r);print(json.dumps(r,allow_nan=False),flush=True)
    report=dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),sources=sources,results=results,operational_ready=False,
        note="Fixed empirical conversion target bounded 0–30, trained on wet cases, weighted by date and liquid amount. Can scale with forecast precipitation rather than saturating at historical RF amounts. Reporting clocks and phase unresolved; effective ratio is not observed SLR. Test winters reused in research; no operational promotion.")
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
if __name__=="__main__":main()
