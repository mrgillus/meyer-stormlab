"""Higher-resolution 00Z live shadow inference, with no old-cycle substitution."""
import datetime as dt
import json
import math
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import regional_live as live
import regional_learning as learn
import storm_blend as storm

ROOT=live.ROOT
OUT=ROOT/"data/regional_live_v2"
WINDOWS=(("20261125",24),("20261125",48))
def hi_path(date,member,lead,part="s"):
    return f"gefs.{date}/00/atmos/pgrb2sp25/ge{member}.t00z.pgrb2s.0p25.f{lead:03d}"

def hi_record(date,member,lead,var,level,step):
    url,rr=live.records(date,member,lead,"s")
    matches=[r for r in rr if r[2][3]==var and r[2][4]==level and r[2][5].strip()==step]
    if len(matches)!=1:raise ValueError(f"Missing or ambiguous 0.25-degree record {date} {member} {lead} {var} {level} {step}")
    return url,matches[0]

# Require the fine-grid product. Never silently mix in the coarser product.
live.path=hi_path
live.record=hi_record
def hi_record(date,member,lead,var,level,step):
    url,rr=live.records(date,member,lead,"s")
    matches=[r for r in rr if r[2][3]==var and r[2][4]==level and r[2][5].strip()==step]
    if not matches:raise ValueError(f"Missing {date} {member} {lead} {var} {level} {step} in 0.25-degree product")
    if len(matches)>1:raise ValueError("Ambiguous 0.25-degree record")
    return url,matches[0]
live.record=hi_record

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((live.ARCH/"manifest.json").read_text())
    units=live.historical_units(manifest)
    points=[dict(p,kind="synoptic" if i<10 else "station") for i,p in enumerate(manifest["points"])]
    points.extend(dict(label=p["name"],lat=p["lat"],lon=p["lon"],kind="town") for p in storm.POINTS if p["kind"]=="town")
    coords=[(p["lat"],p["lon"]) for p in points]
    windows=[];errors=[]
    for date,lead in WINDOWS:
        end=dt.datetime.strptime(date,"%Y%m%d").replace(tzinfo=dt.timezone.utc)+dt.timedelta(hours=lead)
        w=dict(initialization_utc=date+"T00Z",lead_hours=lead,valid_start_utc=(end-dt.timedelta(hours=24)).isoformat(),valid_end_utc=end.isoformat(),status="incomplete",resolution_degrees=.25)
        members={}
        with ThreadPoolExecutor(max_workers=5) as ex:
            for m,f in [(m,ex.submit(live.get_member,date,lead,m,coords)) for m in live.MEMBERS]:
                try:members[m]=f.result()
                except Exception as e:errors.append(dict(date=date,lead=lead,member=m,error=str(e)));print(errors[-1],flush=True)
        if len(members)!=5:windows.append(w);continue
        data=np.stack([members[m] for m in live.MEMBERS],axis=1)
        if data.shape!=(6,5,len(points)):raise ValueError("Feature shape mismatch")
        w["inputs"]=[dict(**p,precip_mm=round(float(data[5,:,i].mean()),3),member_precip_mm={m:round(float(data[5,j,i]),3) for j,m in enumerate(live.MEMBERS)},t2m_c=round(float(data[0,:,i].mean()-273.15),2),t850_c=round(float(data[1,:,i].mean()-273.15),2)) for i,p in enumerate(points) if p["kind"]=="town"]
        live.TRAIN=ROOT/"data"/("regional_learning" if lead==24 else "regional_learning48")
        report_path=live.TRAIN/"report.json"
        if not report_path.exists():w["status"]="awaiting_lead_matched_training";windows.append(w);continue
        report=json.loads(report_path.read_text())
        if report["archive_fingerprint"]!=manifest["fingerprint"]:raise ValueError("Archive fingerprint mismatch")
        if not all(any(r["lead_hours"]==lead and r["label_mapping"]==m and r["status"]=="evaluated_research" for r in report["results"]) for m in learn.MAPPINGS):
            w["status"]="awaiting_sufficient_training_coverage";windows.append(w);continue
        pred=live.infer(date,lead,data,points,report)
        w.update(pred,training_generated_at=report["generated_at"],training_archive_snapshot=report["archive_snapshot_sha256"])
        windows.append(w)
    payload=dict(schema=2,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),archive_fingerprint=manifest["fingerprint"],historical_units=units,windows=windows,errors=errors,operational_ready=False,note="Same 00Z initialization, +24 and +48h leads, preceding 24h precipitation per window. Strict 0.25-degree products. Research only; reporting-time alignment and operational/reforecast differences unresolved. Not a validated 48h storm total.")
    (OUT/"status.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
    if all(w["status"]=="research_only" for w in windows):
        (OUT/"report.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
        (OUT/"captures").mkdir(exist_ok=True)
        (OUT/"captures"/(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".json")).write_text(json.dumps(payload,allow_nan=False))
    print(json.dumps(payload,allow_nan=False),flush=True)
if __name__=="__main__":main()
