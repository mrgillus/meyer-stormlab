"""Evaluate paired +24h/+48h held-out snowfall predictions for the same issue.

This is a reporting-date sensitivity benchmark, not verified storm truth.
The underlying held-out seasons have already been examined in research.
No model weights, operational guidance, or historical labels are changed.
"""
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/storm_total_learning"
MAPPINGS={"utc_end_date":0,"utc_end_previous_date":-1}
PREDICTORS=("rf_in","boosted_in","cold_precip_in")

def read_predictions(path,lead):
    rows={}
    with gzip.open(path,"rt",newline="") as f:
        for r in csv.DictReader(f):
            if int(r["lead_hours"])!=lead:continue
            mapping=r["reported_date_mapping"]
            if mapping not in MAPPINGS:raise ValueError("Unknown reported-date mapping")
            day=dt.date.fromisoformat(r["reported_date"])
            init=day-dt.timedelta(days=lead//24+MAPPINGS[mapping])
            key=(mapping,init,r["station"])
            if key in rows:raise ValueError("Duplicate station/issue/mapping")
            vals={k:float(r[k]) for k in ("actual_in",*PREDICTORS)}
            if any(not math.isfinite(v) or v<0 for v in vals.values()):
                raise ValueError("Invalid amounts")
            rows[key]=(day,vals)
    return rows

def join(a,b):
    cases=[]
    for key in sorted(a.keys()&b.keys()):
        d1,v1=a[key];d2,v2=b[key]
        if d2-d1!=dt.timedelta(days=1):raise ValueError("Nonconsecutive dates")
        mapping,init,station=key
        cases.append(dict(mapping=mapping,initialization=init.isoformat(),station=station,
            reported_start=d1.isoformat(),reported_end=d2.isoformat(),
            **{k:v1[k]+v2[k] for k in v1}))
    return cases

def metrics(rows,predictor):
    if not rows:return None
    # Equal weighting per initialization; nearby station values are correlated.
    counts=Counter(r["initialization"] for r in rows)
    w=[1/counts[r["initialization"]] for r in rows]
    errors=[abs(r[predictor]-r["actual_in"]) for r in rows]
    snow=[i for i,r in enumerate(rows) if r["actual_in"]>=1]
    return dict(issue_weighted_mae=sum(a*b for a,b in zip(w,errors))/sum(w),
        snow_case_mae=sum(w[i]*errors[i] for i in snow)/sum(w[i] for i in snow) if snow else None,
        thresholds={str(t):dict(hits=sum(r[predictor]>=t and r["actual_in"]>=t for r in rows),
            misses=sum(r[predictor]<t and r["actual_in"]>=t for r in rows),
            false_alarms=sum(r[predictor]>=t and r["actual_in"]<t for r in rows)) for t in (1,3,6)})

def main():
    dirs=[ROOT/"data/regional_learning",ROOT/"data/regional_learning48"]
    files=[d/"heldout_predictions.csv.gz" for d in dirs]
    reports=[json.loads((d/"report.json").read_text()) for d in dirs]
    if reports[0]["archive_fingerprint"]!=reports[1]["archive_fingerprint"]:raise ValueError("Incompatible archive fingerprints")
    if reports[0]["seasons"]!=reports[1]["seasons"]:raise ValueError("Incompatible evaluation seasons")
    rows=join(read_predictions(files[0],24),read_predictions(files[1],48))
    if not rows:raise ValueError("No paired held-out predictions")
    summary=[]
    for mapping in MAPPINGS:
        subset=[r for r in rows if r["mapping"]==mapping]
        for r in subset:r["no_snow"]=0
        summary.append(dict(mapping=mapping,paired_station_windows=len(subset),
            initializations=len({r["initialization"] for r in subset}),
            stations=len({r["station"] for r in subset}),
            snow_windows=sum(r["actual_in"]>=1 for r in subset),
            metrics={p:metrics(subset,p) for p in (*PREDICTORS,"no_snow")}))
    OUT.mkdir(parents=True,exist_ok=True)
    inputs=[dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        training_generated_at=r["generated_at"],complete_shards=r["complete_shards"]) for p,r in zip(files,reports)]
    report=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        inputs=inputs,results=summary,operational_ready=False,
        method="Same-issue +24/+48h predictions summed; two consecutive reported station dates summed; missing pairs excluded. Both prespecified mappings retained. No observation-driven event selection.",
        limitations=["48-hour reported-date windows are not verified UTC accumulation windows.",
        "These are fixed two-day windows, not necessarily entire storms.",
        "Holdout winters reused from previous research; not independent final validation.",
        "Overlapping issue windows and nearby stations are correlated. No calibrated probabilities."])
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps(report,allow_nan=False),flush=True)

if __name__=="__main__":main()
