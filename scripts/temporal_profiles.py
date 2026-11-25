"""Bounded historical/live pilot for six-hour liquid precipitation and thermal evolution.
Exact nonoverlapping accumulation periods, no inferred snow phase or SLR."""
import datetime as dt
import gzip
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import atmospheric_profiles as p
import regional_live as live
import regional_live_v2 as v2
import regional_archive as src
ROOT=live.ROOT
OUT=ROOT/"data/temporal_profiles"
NEEDED={"t2m","t925","t850","t700","t600","t500","q925","q850","q700","q600","q500","omega700","omega600","skin","surface_pressure"}
SPECS=[s for s in p.SPECS if s[0] in NEEDED]
def liquid(mode,date,member,end,points):
    coords=[(q["lat"],q["lon"]) for q in points]
    if mode=="historical":
        url=src.file_url(date,member,end,"apcp_sfc")
        rr=p.index(url)
        matches=[r for r in rr if r[2][3]=="APCP" and r[2][5].strip()==f"{end-6}-{end} hour acc fcst"]
        if len(matches)!=1:raise ValueError("Historical 6h block missing")
        blocks=[(end,url,matches[0])]
    else:
        blocks=[]
        try:
            url,rec=v2.hi_record(date,member,end,"APCP","surface",f"{end-6}-{end} hour acc fcst")
            blocks.append((end,url,rec))
        except ValueError as e:
            if not str(e).startswith("Missing "):raise
            for t in (end-3,end):
                url,rec=v2.hi_record(date,member,t,"APCP","surface",f"{t-3}-{t} hour acc fcst")
                blocks.append((t,url,rec))
    values=[]
    for t,url,r in blocks:
        d=src.decode(url,r,coords)
        if d["data_date"]!=int(date) or d["data_time"]!=0 or d["end_step"]!=t:raise ValueError("Liquid provenance mismatch")
        values.append(live.canonical("precip24",d["units"],d["values"]))
    return np.sum(values,axis=0).tolist()
def task(mode,date,member,end,points):
    rows={};errors={}
    for s in SPECS:
        try:
            url,r=p.select(mode,date,member,end,s)
            rows[s[0]]=p.decode(url,r,date,end,s,points)
        except Exception as e:errors[s[0]]=str(e)
    try:rain=liquid(mode,date,member,end,points)
    except Exception as e:rain=None;errors["liquid"]=str(e)
    return dict(date=date,member=member,start_hour=end-6,end_hour=end,fields=rows,liquid_mm=rain,errors=errors)
def main():
    manifest=json.loads((live.ARCH/"manifest.json").read_text())
    points=[dict(x,kind="synoptic" if i<10 else "station") for i,x in enumerate(manifest["points"])]
    points.extend(dict(label=x["name"],lat=x["lat"],lon=x["lon"],kind="town") for x in v2.storm.POINTS if x["kind"]=="town")
    # Two historical pilot dates with one control each; 5 live members.
    jobs=[("historical",d,"c00",h) for d in ("20000105","20190108") for h in range(6,49,6)]
    jobs += [("live","20261125",m,h) for m in live.MEMBERS for h in range(6,49,6)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        records=[dict(mode=j[0],**f.result()) for j,f in [(j,ex.submit(task,*j,points)) for j in jobs]]
    summaries=[]
    for mode in ("historical","live"):
        rr=[r for r in records if r["mode"]==mode]
        summaries.append(dict(mode=mode,blocks=len(rr),liquid_blocks=sum(r["liquid_mm"] is not None for r in rr),
            field_coverage={s[0]:sum(s[0] in r["fields"] for r in rr) for s in SPECS},errors=sum(len(r["errors"]) for r in rr)))
    towns=[]
    for r in records:
        if r["mode"]!="live":continue
        for i,x in enumerate(points):
            if x["kind"]!="town":continue
            def val(f):
                return r["fields"][f]["values"][i] if f in r["fields"] else None
            temp={f:round(val(f)-273.15,2) if val(f) is not None else None for f in ("t2m","t925","t850","t700","t600","t500","skin")}
            towns.append(dict(town=x["label"],member=r["member"],start_hour=r["start_hour"],end_hour=r["end_hour"],liquid_mm=r["liquid_mm"][i] if r["liquid_mm"] is not None else None,endpoint_temp_c=temp))
    OUT.mkdir(parents=True,exist_ok=True)
    with gzip.open(OUT/"pilot.json.gz","wt") as f:json.dump(dict(points=points,records=records),f,separators=(",",":"),allow_nan=False)
    report=dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),archive_fingerprint=manifest["fingerprint"],summaries=summaries,towns=towns,operational_ready=False,
        note="Six-hour precipitation paired with instantaneous endpoint profiles. Endpoint conditions do not establish phase throughout the block; historical pilot too small for ML. No snow totals inferred.")
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps(summaries),flush=True)
if __name__=="__main__":main()
