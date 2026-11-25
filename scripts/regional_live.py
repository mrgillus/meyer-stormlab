"""Live operational GEFS shadow inference with the historical regional feature schema.
Never substitute mismatched leads, UTC windows, ensemble members or fields.
"""
import datetime as dt
import gzip
import json
import math
import hashlib
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import joblib
import regional_archive as src
import regional_learning as learn
import storm_blend as storm

ROOT=Path(__file__).resolve().parents[1]
ARCH=ROOT/"data/gefs_archive"
TRAIN=ROOT/"data/regional_learning"
OUT=ROOT/"data/regional_live"
BASE="https://noaa-gefs-pds.s3.amazonaws.com/"
NOMADS="https://nomads.ncep.noaa.gov/pub/data/nccf/com/gens/prod/"
MEMBERS=learn.MEMBERS
FIELDS=learn.FIELDS
# Adjacent 24h UTC windows: Tue 6pm–Wed 6pm and Wed 6pm–Thu 6pm CST.
# No trained 48h model exists yet; the second window uses the prior 00Z 72h run.
WINDOWS=(("20261125",24),("20261124",72))
VARS={"t2m":("TMP","2 m above ground"),"t850":("TMP","850 mb"),
      "z500":("HGT","500 mb"),"mslp":("PRMSL","mean sea level"),
      "pwat":("PWAT","entire atmosphere (considered as a single layer)")}
def path(date,member,lead,part="a"):
    return f"gefs.{date}/00/atmos/pgrb2{part}p5/ge{member}.t00z.pgrb2{part}.0p50.f{lead:03d}"
@lru_cache(maxsize=128)
def records(date,member,lead,part):
    suffix=path(date,member,lead,part)
    errors=[]
    for base in (BASE,NOMADS):
        url=base+suffix
        try:return url,src.index(url)
        except Exception as e:errors.append(str(e))
    raise RuntimeError(" | ".join(errors))
def record(date,member,lead,var,level,step):
    for part in ("a","b"):
        try:url,rr=records(date,member,lead,part)
        except Exception:continue
        matches=[r for r in rr if r[2][3]==var and r[2][4]==level and r[2][5].strip()==step]
        if len(matches)==1:return url,matches[0]
        if len(matches)>1:raise ValueError("Ambiguous GRIB index match")
    raise ValueError(f"Missing {date} {member} {lead} {var} {level} {step}")
def checked(date,member,lead,var,level,step,points):
    url,rec=record(date,member,lead,var,level,step)
    d=src.decode(url,rec,points)
    if d["data_date"]!=int(date) or d["data_time"]!=0 or d["end_step"]!=lead:
        raise ValueError("Operational GRIB initialization or lead mismatch")
    return d
def canonical(field,units,values):
    a=np.asarray(values,float)
    if field in ("t2m","t850"):
        if units not in ("K","Kelvin"):raise ValueError("Temperature units "+units)
    elif field=="z500":
        if units not in ("gpm","m"):raise ValueError("Height units "+units)
    elif field=="mslp":
        if units=="hPa":a*=100
        elif units!="Pa":raise ValueError("Pressure units "+units)
    elif field in ("pwat","precip24"):
        if units not in ("kg m**-2","kg m-2","mm","mm water equivalent"):raise ValueError("Water units "+units)
        if (a<-.001).any():raise ValueError("Negative water field")
        a=np.maximum(a,0)
    if not np.isfinite(a).all():raise ValueError("Nonfinite operational field")
    return a
def historical_units(manifest):
    # Check actual archived metadata and units before using the feature schema.
    signatures={f:set() for f in FIELDS}
    count=0
    for p in sorted((ARCH/"shards").glob("*/*.json.gz")):
        with gzip.open(p,"rt") as f:d=json.load(f)
        if not d.get("core_complete") or d.get("fingerprint")!=manifest["fingerprint"]:continue
        for r in d["rows"]:
            if r["lead_hours"] in (24,72):
                signatures[r["field"]].add(r["units"])
                # Historical models were trained on raw archived values, so reject a
                # conversion that would change the scale of those training inputs.
                if r["field"]=="mslp" and r["units"]!="Pa":raise ValueError("Historical MSLP not Pa")
                canonical(r["field"],r["units"],r["values"])
        count+=1
        if count>=8:break
    if count<1 or not all(signatures.values()):raise ValueError("Cannot confirm archive units")
    return {f:sorted(v) for f,v in signatures.items()}
def get_member(date,lead,member,points):
    data={}
    for f,(var,level) in VARS.items():
        d=checked(date,member,lead,var,level,f"{lead} hour fcst",points)
        data[f]=canonical(f,d["units"],d["values"])
    blocks=[]
    for end in range(lead-18,lead+1,6):
        try:
            d=checked(date,member,end,"APCP","surface",f"{end-6}-{end} hour acc fcst",points)
            blocks.append(canonical("precip24",d["units"],d["values"]))
        except ValueError as e:
            # At short leads some products publish only 3-hour increments.
            # Only use two exact, nonoverlapping increments, never a cumulative substitute.
            if not str(e).startswith("Missing "):raise
            for t in (end-3,end):
                d=checked(date,member,t,"APCP","surface",f"{t-3}-{t} hour acc fcst",points)
                blocks.append(canonical("precip24",d["units"],d["values"]))
    data["precip24"]=np.sum(blocks,axis=0)
    return np.stack([data[f] for f in FIELDS],axis=0)

def vector(data,points,i,target):
    mean=data.mean(axis=1);sd=data.std(axis=1)
    syn=np.concatenate((mean[:,:10].ravel(),sd[:,:10].ravel()))
    p=points[i];doy=target.timetuple().tm_yday
    local=np.concatenate((mean[:,i],sd[:,i],[p["lat"],p["lon"],
        math.sin(doy*2*math.pi/365.25),math.cos(doy*2*math.pi/365.25),
        float(np.mean(data[0,:,i]<=273.15)),float(np.mean(data[1,:,i]<=273.15))]))
    return np.concatenate((local,syn))

def infer(date,lead,data,points,report):
    end=dt.datetime.strptime(date,"%Y%m%d").replace(tzinfo=dt.timezone.utc)+dt.timedelta(hours=lead)
    start=end-dt.timedelta(hours=24)
    out=dict(initialization_utc=date+"T00Z",lead_hours=lead,valid_start_utc=start.isoformat(),
        valid_end_utc=end.isoformat(),status="research_only",mappings=[])
    for mapping,offset in learn.MAPPINGS.items():
        target=end.date()+dt.timedelta(days=offset)
        meta=next(r for r in report["results"] if r["lead_hours"]==lead and r["label_mapping"]==mapping)
        if meta["status"]!="evaluated_research":continue
        model_path=TRAIN/f"research_{lead}_{mapping}.joblib"
        m=joblib.load(model_path)
        if m["feature_version"]!=1 or tuple(m["fields"])!=FIELDS or tuple(m["members"])!=MEMBERS or m["lead_hours"]!=lead or m["label_mapping"]!=mapping:raise ValueError("Trained model schema mismatch")
        ix=list(range(10,len(points)))
        X=np.stack([vector(data,points,i,target) for i in ix])
        if X.shape[1]!=m["rf"].n_features_in_:raise ValueError("Feature width mismatch")
        lo=m["training_min"];hi=m["training_max"];tol=np.maximum(1e-6,(hi-lo)*.001)
        exceed=(X<lo-tol)|(X>hi+tol)
        rf=m["rf"].predict(X);boost=m["boosted"].predict(X)
        prob=m["classifier"].predict_proba(X)[:,list(m["classifier"].classes_).index(True)]
        rows=[]
        for j,i in enumerate(ix):
            p=points[i];flags=np.flatnonzero(exceed[j]).tolist()
            # Show shadow outputs, even when out-of-domain, but explicitly tag them;
            # these are not operational predictions, calibrated odds or observed totals.
            rows.append(dict(**p,rf_inches=round(float(rf[j]),3),boosted_inches=round(float(boost[j]),3),
                uncalibrated_p_ge1=round(float(prob[j]),4),
                outside_training_features=flags,status="outside_training_range" if flags else "research_only",
                mean_t2m_c=round(float(data[0,:,i].mean()-273.15),2),
                mean_t850_c=round(float(data[1,:,i].mean()-273.15),2),
                mean_precip24_mm=round(float(data[5,:,i].mean()),2)))
        out["mappings"].append(dict(label_mapping=mapping,reported_date_proxy=target.isoformat(),
            points=rows,heldout_metrics=meta["metrics"]["test"]))
    return out

def main():
    now=dt.datetime.now(dt.timezone.utc)
    manifest=json.loads((ARCH/"manifest.json").read_text())
    report=json.loads((TRAIN/"report.json").read_text())
    if report["archive_fingerprint"]!=manifest["fingerprint"]:raise ValueError("Training archive fingerprint mismatch")
    if tuple(manifest["members"])!=MEMBERS or tuple(manifest["fields"])!=FIELDS:raise ValueError("Feature manifest mismatch")
    units=historical_units(manifest)
    points=[dict(p,kind="synoptic" if i<10 else "station") for i,p in enumerate(manifest["points"])]
    points.extend(dict(label=p["name"],lat=p["lat"],lon=p["lon"],kind="town") for p in storm.POINTS if p["kind"]=="town")
    coords=[(p["lat"],p["lon"]) for p in points]
    windows=[];errors=[]
    for date,lead in WINDOWS:
        members={}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures=[(m,pool.submit(get_member,date,lead,m,coords)) for m in MEMBERS]
            for m,f in futures:
                try:members[m]=f.result()
                except Exception as e:
                    errors.append(dict(date=date,lead_hours=lead,member=m,error=str(e)))
                    print("ERROR",errors[-1],flush=True)
        if len(members)!=len(MEMBERS):
            windows.append(dict(initialization_utc=date+"T00Z",lead_hours=lead,status="incomplete",members=list(members)))
            continue
        data=np.stack([members[m] for m in MEMBERS],axis=1)
        if data.shape!=(6,5,len(points)):raise ValueError("Live feature shape mismatch")
        windows.append(infer(date,lead,data,points,report))
        print("Complete",date,lead,flush=True)
    payload=dict(schema=1,generated_at=now.isoformat(),training_generated_at=report["generated_at"],
        training_archive_snapshot=report["archive_snapshot_sha256"],archive_fingerprint=manifest["fingerprint"],
        historical_units=units,windows=windows,errors=errors,operational_ready=False,
        note="Shadow inference only. Operational GEFS 0.5-degree grid may differ from reforecast grid/model version. Two unverified reported-date mappings are shown separately. 24-hour windows are UTC, not local daily totals. Classifier scores are uncalibrated. No validated storm-total or official forecast.")
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"status.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
    if all(w["status"]=="research_only" for w in windows):
        # Preserve provenance and immutable captures; no summing unaligned reported-date labels.
        (OUT/"report.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
        (OUT/"captures").mkdir(exist_ok=True)
        (OUT/"captures"/(now.strftime("%Y%m%dT%H%M%SZ")+".json")).write_text(json.dumps(payload,separators=(",",":"),allow_nan=False)+"\n")
    else:print("::warning::Incomplete GEFS shadow inputs; last complete report retained",flush=True)
if __name__=="__main__":main()
