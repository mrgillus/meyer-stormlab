"""Higher-resolution 00Z live shadow inference, with no old-cycle substitution."""
import datetime as dt
import hashlib
import json
import math
import sys
import joblib
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import regional_live as live
import regional_learning as learn
import storm_blend as storm

ROOT=live.ROOT
OUT=ROOT/"data/regional_live_v2"
def forecast_windows(now):
    """Select latest eligible 00Z cycle after an eight-hour availability lag."""
    if now.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    now=now.astimezone(dt.timezone.utc)
    cycle=(now-dt.timedelta(hours=8)).date()
    return [(cycle.strftime("%Y%m%d"),lead) for lead in (24,48,72,168)
            if dt.datetime.combine(cycle,dt.time.min,tzinfo=dt.timezone.utc)
            +dt.timedelta(hours=lead)>now]
# NOAA's fine-grid subset lacks some pressure-level fields. Record the
# actual product per field and never fall back to coarse precipitation.
USED={}
def hi_path(date,member,lead,part="s"):
    folder,res=("pgrb2sp25","0p25") if part=="s" else (f"pgrb2{part}p5","0p50")
    return f"gefs.{date}/00/atmos/{folder}/ge{member}.t00z.pgrb2{part}.{res}.f{lead:03d}"
live.path=hi_path

def hi_record(date,member,lead,var,level,step):
    for part in (("s",) if var=="APCP" else ("s","a","b")):
        url,rr=live.records(date,member,lead,part)
        matches=[r for r in rr if r[2][3]==var and r[2][4]==level and r[2][5].strip()==step]
        if len(matches)>1:raise ValueError("Ambiguous GRIB record")
        if len(matches)==1:
            USED[(date,member,lead,var,level)]=.25 if part=="s" else .5
            return url,matches[0]
    raise ValueError(f"Missing {date} {member} {lead} {var} {level} {step}")
live.record=hi_record

# Raw archive units the trained models consume (no conversion is applied).
FEATURE_UNITS={"t2m":"K","t850":"K","z500":"gpm","mslp":"Pa","pwat":"kg m**-2","precip24":"mm"}
SYNOPTIC_POINTS=10

def feature_names(points):
    """Human-readable names for regional_learning feature_version 1, in model order.

    Mirrors regional_learning.load()/regional_live.vector(): local ensemble mean and
    spread, location/season terms, then field-major synoptic mean and spread.
    """
    if len(points)<SYNOPTIC_POINTS:raise ValueError("Synoptic points missing")
    fields=learn.FIELDS
    names=[(f"local_mean_{f}",FEATURE_UNITS[f]) for f in fields]
    names+=[(f"local_sd_{f}",FEATURE_UNITS[f]) for f in fields]
    names+=[("lat","deg"),("lon","deg"),("doy_sin","1"),("doy_cos","1"),
            ("member_fraction_t2m_le_0C","1"),("member_fraction_t850_le_0C","1")]
    syn=[(p["label"],p["lat"],p["lon"]) for p in points[:SYNOPTIC_POINTS]]
    for stat in ("mean","sd"):
        names+=[(f"synoptic_{stat}_{f}@{label}({lat:g},{lon:g})",FEATURE_UNITS[f]) for f in fields for label,lat,lon in syn]
    return names

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def annotate(window,data,points,train_dir):
    """Attach artifact provenance and named, quantified out-of-range diagnostics.

    Recomputes the exact feature vectors used by regional_live.infer and verifies the
    flags agree, so a published flag always carries its feature, value and bounds.
    The safeguard itself is unchanged: bounds are never widened here.
    """
    names=feature_names(points)
    lead=window["lead_hours"]
    ix=list(range(SYNOPTIC_POINTS,len(points)))
    for mapping in window.get("mappings",[]):
        path=train_dir/f"research_{lead}_{mapping['label_mapping']}.joblib"
        model=joblib.load(path)
        target=dt.date.fromisoformat(mapping["reported_date_proxy"])
        X=np.stack([live.vector(data,points,i,target) for i in ix])
        if X.shape[1]!=len(names):raise ValueError("Feature name schema mismatch")
        lo=model["training_min"];hi=model["training_max"];tol=np.maximum(1e-6,(hi-lo)*.001)
        exceed=(X<lo-tol)|(X>hi+tol)
        summary={}
        if len(mapping["points"])!=len(ix):raise ValueError("Point count mismatch")
        for j,row in enumerate(mapping["points"]):
            if row["label"]!=points[ix[j]]["label"]:raise ValueError("Point order mismatch")
            flags=np.flatnonzero(exceed[j]).tolist()
            if flags!=row["outside_training_features"]:raise ValueError("Range flag recomputation mismatch")
            row["outside_training_detail"]=[dict(index=k,name=names[k][0],units=names[k][1],value=round(float(X[j,k]),4),
                training_min=round(float(lo[k]),4),training_max=round(float(hi[k]),4),
                direction="above" if X[j,k]>hi[k] else "below") for k in flags]
            for d in row["outside_training_detail"]:
                s=summary.setdefault(d["index"],dict(index=d["index"],name=d["name"],units=d["units"],
                    shared_synoptic_feature=d["index"]>=18,training_min=d["training_min"],training_max=d["training_max"],
                    points_flagged=0,live_min=d["value"],live_max=d["value"]))
                s["points_flagged"]+=1;s["live_min"]=min(s["live_min"],d["value"]);s["live_max"]=max(s["live_max"],d["value"])
        mapping["model_artifact"]=dict(path=path.relative_to(ROOT).as_posix(),sha256=sha256(path),
            feature_version=model["feature_version"],train_seasons=model["train_seasons"])
        mapping["out_of_range_summary"]=[summary[k] for k in sorted(summary)]
    return window

def training_reports():
    out={}
    for lead in (24,48,72,168):
        path=ROOT/"data"/("regional_learning48" if lead==48 else "regional_learning")/"report.json"
        if path.exists():out[lead]=json.loads(path.read_text())["generated_at"]
    return out

def artifacts_already_consumed(status_path=None, now=None):
    """Skip only if every currently eligible window succeeded with current artifacts."""
    status_path=status_path or OUT/"status.json"
    if not status_path.exists():return False
    try:status=json.loads(status_path.read_text())
    except (OSError,ValueError):return False
    current=training_reports()
    expected=forecast_windows(now or dt.datetime.now(dt.timezone.utc))
    if not expected:return False
    windows={(w.get("initialization_utc"),w.get("lead_hours")):w
             for w in status.get("windows",[])}
    for date,lead in expected:
        w=windows.get((date+"T00Z",lead),{})
        if (w.get("status")!="research_only" or not current.get(lead)
                or w.get("training_generated_at")!=current[lead]):
            return False
    return True

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((live.ARCH/"manifest.json").read_text())
    units=live.historical_units(manifest)
    points=[dict(p,kind="synoptic" if i<10 else "station") for i,p in enumerate(manifest["points"])]
    points.extend(dict(label=p["name"],lat=p["lat"],lon=p["lon"],kind="town") for p in storm.POINTS if p["kind"]=="town")
    coords=[(p["lat"],p["lon"]) for p in points]
    windows=[];errors=[]
    started=dt.datetime.now(dt.timezone.utc)
    for date,lead in forecast_windows(started):
        end=dt.datetime.strptime(date,"%Y%m%d").replace(tzinfo=dt.timezone.utc)+dt.timedelta(hours=lead)
        w=dict(initialization_utc=date+"T00Z",lead_hours=lead,valid_start_utc=(end-dt.timedelta(hours=24)).isoformat(),valid_end_utc=end.isoformat(),status="incomplete",precipitation_resolution_degrees=.25)
        members={}
        with ThreadPoolExecutor(max_workers=5) as ex:
            for m,f in [(m,ex.submit(live.get_member,date,lead,m,coords)) for m in live.MEMBERS]:
                try:members[m]=f.result()
                except Exception as e:errors.append(dict(date=date,lead=lead,member=m,error=str(e)));print(errors[-1],flush=True)
        w["field_resolution_degrees"]={f:sorted({res for (d,m,l,v,lev),res in USED.items() if d==date and l==lead and v==var and lev==level}) for f,(var,level) in live.VARS.items()}
        w["field_resolution_degrees"]["precip24"]=sorted({res for (d,m,l,v,lev),res in USED.items() if d==date and lead-24<l<=lead and v=="APCP"})
        if len(members)!=5:windows.append(w);continue
        if any(len(v)!=1 for v in w["field_resolution_degrees"].values()):raise ValueError("Inconsistent ensemble field resolutions")
        data=np.stack([members[m] for m in live.MEMBERS],axis=1)
        if data.shape!=(6,5,len(points)):raise ValueError("Feature shape mismatch")
        w["inputs"]=[dict(**p,precip_mm=round(float(data[5,:,i].mean()),3),member_precip_mm={m:round(float(data[5,j,i]),3) for j,m in enumerate(live.MEMBERS)},t2m_c=round(float(data[0,:,i].mean()-273.15),2),t850_c=round(float(data[1,:,i].mean()-273.15),2)) for i,p in enumerate(points) if p["kind"]=="town"]
        live.TRAIN=ROOT/"data"/("regional_learning48" if lead==48 else "regional_learning")
        report_path=live.TRAIN/"report.json"
        if not report_path.exists():w["status"]="awaiting_lead_matched_training";windows.append(w);continue
        report=json.loads(report_path.read_text())
        if report["archive_fingerprint"]!=manifest["fingerprint"]:raise ValueError("Archive fingerprint mismatch")
        if not all(any(r["lead_hours"]==lead and r["label_mapping"]==m and r["status"]=="evaluated_research" for r in report["results"]) for m in learn.MAPPINGS):
            w["status"]="awaiting_sufficient_training_coverage";windows.append(w);continue
        pred=annotate(live.infer(date,lead,data,points,report),data,points,live.TRAIN)
        w.update(pred,training_generated_at=report["generated_at"],training_archive_snapshot=report["archive_snapshot_sha256"])
        windows.append(w)
    payload=dict(schema=2,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),archive_fingerprint=manifest["fingerprint"],historical_units=units,windows=windows,errors=errors,operational_ready=False,note="Latest eligible 00Z initialization, unexpired +24/+48/+72/+168h leads, preceding 24h precipitation per window. Precipitation strictly 0.25-degree; missing fine-grid atmospheric fields use explicitly recorded 0.5-degree products. Mixed-resolution inputs are research-only. Research only; reporting-time alignment and operational/reforecast differences unresolved. Not a validated 48h storm total.")
    (OUT/"status.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
    (OUT/"captures").mkdir(exist_ok=True)
    (OUT/"captures"/(started.strftime("%Y%m%dT%H%M%SZ")+".json")).write_text(json.dumps(payload,allow_nan=False))
    if windows and all(w["status"]=="research_only" for w in windows):
        (OUT/"report.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
    print(json.dumps(payload,allow_nan=False),flush=True)
if __name__=="__main__":
    if "--if-stale" in sys.argv[1:] and artifacts_already_consumed():
        print("Shadow inference already used the current training artifacts; skipping.",flush=True)
    else:main()
