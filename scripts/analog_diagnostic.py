"""Experimental atmospheric analog search. No snowfall outcome claims.
Normalize using only historical candidate initializations earlier than query.
"""
import datetime as dt
import gzip
import json
import math
import pathlib
import statistics as st
from collections import defaultdict

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"gefs_archive"
FIELDS=("t2m","t850","z500","mslp","pwat","precip24")
LEADS=(24,72,168)
MEMBERS=("c00","p01","p02","p03","p04")
def date(v):return dt.datetime.strptime(v,"%Y%m%d").date()
def season(d):
    return d.year if d.month>=9 else d.year-1
def vector(shard,lead):
    lookup={(r["member"],r["lead_hours"],r["field"]):r for r in shard["rows"]}
    values=[]
    for field in FIELDS:
        rows=[lookup.get((m,lead,field)) for m in MEMBERS]
        if not all(rows):return None
        # First ten locations are the fixed synoptic network.
        cols=list(zip(*(r["values"][:10] for r in rows)))
        if len(cols)!=10:return None
        values.extend(st.mean(c) for c in cols)
        values.extend(st.pstdev(c) for c in cols)
    return values
def main():
    progress=json.loads((OUT/"progress.json").read_text())
    manifest=json.loads((OUT/"manifest.json").read_text())
    shards=[]
    for p in sorted((OUT/"shards").glob("*/*.json.gz")):
        with gzip.open(p,"rt") as f:r=json.load(f)
        if r.get("fingerprint")==manifest["fingerprint"] and r["core_complete"]:shards.append(r)
    # One recent archived initialization as a reproducible diagnostic query.
    shards.sort(key=lambda x:x["date"])
    results=[]
    if shards:
        q=shards[-1];qdate=date(q["date"])
        for lead in LEADS:
            target=vector(q,lead)
            # Prevent future data use and near-event duplication. Seasonally similar dates only.
            eligible=[]
            for r in shards[:-1]:
                d=date(r["date"])
                if season(d)>=season(qdate):continue
                doy=(d.month,d.day); a=dt.date(2000,*doy);b=dt.date(2000,qdate.month,qdate.day)
                gap=abs((a-b).days)
                if min(gap,366-gap)>45:continue
                v=vector(r,lead)
                if v is not None:eligible.append((r["date"],v))
            if len(eligible)<30 or target is None:
                results.append(dict(lead_hours=lead,candidates=len(eligible),status="insufficient_candidates",matches=[]));continue
            cols=list(zip(*(v for _,v in eligible)))
            means=[st.mean(c) for c in cols];scale=[st.pstdev(c) for c in cols]
            ix=[i for i,s in enumerate(scale) if s>1e-9]
            if not ix:
                results.append(dict(lead_hours=lead,candidates=len(eligible),status="no_variable_features",matches=[]));continue
            # Candidate-only normalization; distance has no calibrated probabilistic meaning.
            def dist(v):return math.sqrt(st.mean(((v[i]-target[i])/scale[i])**2 for i in ix))
            ranked=sorted((dist(v),d) for d,v in eligible)
            matches=[]
            for score,d in ranked:
                if any(abs((date(d)-date(m["init"])).days)<7 for m in matches):continue
                matches.append(dict(init=d,distance=round(score,4)))
                if len(matches)==10:break
            results.append(dict(lead_hours=lead,candidates=len(eligible),status="pattern_matches",matches=matches))
    report=dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),query_initialization=shards[-1]["date"] if shards else None,
        complete_initializations=progress["core_complete"],results=results,
        note="Pattern similarity only; not a snowfall prediction. Uses earlier winter seasons and candidate-only scaling; labels remain blocked pending observation timing alignment.")
    (OUT/"analogs.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report),flush=True)
if __name__=="__main__":main()
