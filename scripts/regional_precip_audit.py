"""Independent precipitation audit; never alters training or operational guidance."""
import datetime as dt
import json
import math
from pathlib import Path
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from eccodes import codes_new_from_message, codes_release, codes_get, codes_get_array, codes_get_values, codes_grib_find_nearest
import regional_archive as src
import regional_live as live
import storm_blend as storm

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/regional_live"
TOWNS=[p for p in storm.POINTS if p["kind"]=="town"]
COORDS=[(p["lat"],p["lon"]) for p in TOWNS]
WINDOWS=[("20261125",24),("20261124",72),("20261125",48)]
MEMBERS=list(live.MEMBERS)+["avg"]
BASES=(live.BASE,live.NOMADS)

def suffix(date,member,lead,res):
    part,folder=("s","pgrb2sp25") if res=="0p25" else ("a","pgrb2ap5")
    return f"gefs.{date}/00/atmos/{folder}/ge{member}.t00z.pgrb2{part}.{res}.f{lead:03d}"

@lru_cache(maxsize=512)
def get_index(date,member,lead,res):
    failures=[]
    for base in BASES:
        url=base+suffix(date,member,lead,res)
        try:return url,src.index(url)
        except Exception as e:failures.append(str(e))
    raise RuntimeError("Index unavailable: "+" | ".join(failures))

def find(date,member,end,res,start):
    url,rows=get_index(date,member,end,res)
    matches=[r for r in rows if r[2][3]=="APCP" and r[2][4]=="surface" and r[2][5].strip()==f"{start}-{end} hour acc fcst"]
    if len(matches)>1:raise ValueError("Ambiguous accumulation")
    return (url,matches[0]) if matches else None

def independent(url,rec,date,start,end,res):
    off,last,_=rec
    response=src.fetch(url,{"Range":f"bytes={off}-{'' if last is None else last}"})
    if response.status_code!=206 or not response.headers.get("Content-Range","").startswith(f"bytes {off}-"):raise ValueError("Unexpected byte range")
    raw=response.content
    if not raw.startswith(b"GRIB") or not raw.endswith(b"7777"):raise ValueError("Bad GRIB framing")
    g=codes_new_from_message(raw)
    try:
        meta={k:codes_get(g,k) for k in ("dataDate","dataTime","startStep","endStep","stepType","units","shortName","iDirectionIncrementInDegrees","jDirectionIncrementInDegrees")}
        if meta["dataDate"]!=int(date) or meta["dataTime"]!=0 or meta["startStep"]!=start or meta["endStep"]!=end or meta["stepType"]!="accum":raise ValueError("Unexpected accumulation metadata "+str(meta))
        if meta["units"] not in ("kg m**-2","kg m-2","mm"):raise ValueError("Unexpected water units "+str(meta))
        expect=.25 if res=="0p25" else .5
        if any(abs(meta[k]-expect)>1e-6 for k in ("iDirectionIncrementInDegrees","jDirectionIncrementInDegrees")):raise ValueError("Unexpected grid spacing")
        # Independently compute nearest grid point from decoded coordinate arrays.
        lat=np.asarray(codes_get_array(g,"latitudes"));lon=np.asarray(codes_get_array(g,"longitudes"));v=np.asarray(codes_get_values(g))
        if not(len(lat)==len(lon)==len(v)):raise ValueError("Inconsistent decoded grid")
        rows=[]
        for a,b in COORDS:
            dist=np.sin(np.deg2rad(lat-a)/2)**2+np.cos(np.deg2rad(a))*np.cos(np.deg2rad(lat))*np.sin(np.deg2rad(lon-b)/2)**2
            i=int(np.argmin(dist))
            value=float(v[i])
            native=codes_grib_find_nearest(g,a,b%360)[0]
            if not math.isfinite(value) or not 0<=value<1000:raise ValueError("Bad local APCP value")
            rows.append(dict(value=value,lat=float(lat[i]),lon=float((lon[i]+180)%360-180),
                native_value=float(native["value"]),native_lat=float(native["lat"]),native_lon=float((native["lon"]+180)%360-180),
                difference_mm=round(value-float(native["value"]),6)))
        return dict(url=url,start=start,end=end,metadata=meta,points=rows)
    finally:codes_release(g)

def audit(date,lead,member,res):
    blocks=[]
    for end in range(lead-18,lead+1,6):
        rec=find(date,member,end,res,end-6)
        if rec is not None:blocks.append(independent(*rec,date,end-6,end,res))
        else:
            for t in (end-3,end):
                rec=find(date,member,t,res,t-3)
                if rec is None:raise ValueError(f"Missing exact APCP window {t-3}-{t}")
                blocks.append(independent(*rec,date,t-3,t,res))
    if [(b["start"],b["end"]) for b in blocks]!=sorted((b["start"],b["end"]) for b in blocks) or blocks[0]["start"]!=lead-24 or blocks[-1]["end"]!=lead or any(a["end"]!=b["start"] for a,b in zip(blocks,blocks[1:])):raise ValueError("Noncontiguous window")
    totals=[]
    for i,p in enumerate(TOWNS):
        rows=[b["points"][i] for b in blocks]
        totals.append(dict(town=p["name"],total_mm=round(sum(r["value"] for r in rows),4),
            native_total_mm=round(sum(r["native_value"] for r in rows),4),grid_lat=rows[0]["lat"],grid_lon=rows[0]["lon"],
            max_decode_difference_mm=max(abs(r["difference_mm"]) for r in rows)))
    return dict(date=date,lead_hours=lead,member=member,resolution=res,totals=totals,blocks=blocks)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    results=[];errors=[]
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures=[((d,l,m,r),ex.submit(audit,d,l,m,r)) for d,l in WINDOWS for r in ("0p50","0p25") for m in MEMBERS]
        for key,f in futures:
            try:results.append(f.result());print("Audited",key,flush=True)
            except Exception as e:errors.append(dict(date=key[0],lead_hours=key[1],member=key[2],resolution=key[3],error=str(e)));print("ERROR",errors[-1],flush=True)
    summaries=[]
    prior=json.loads((OUT/"report.json").read_text())
    for date,lead in WINDOWS:
        for res in ("0p50","0p25"):
            selected=[r for r in results if r["date"]==date and r["lead_hours"]==lead and r["resolution"]==res]
            five=[r for r in selected if r["member"] in live.MEMBERS]
            avg=next((r for r in selected if r["member"]=="avg"),None)
            old=next((w for w in prior["windows"] if w["initialization_utc"]==date+"T00Z" and w["lead_hours"]==lead),None)
            oldtowns={p["label"]:p for p in old["mappings"][0]["points"] if p["kind"]=="town"} if old and old.get("mappings") else {}
            towns=[]
            for i,p in enumerate(TOWNS):
                vals=[r["totals"][i]["total_mm"] for r in five]
                mean=round(float(np.mean(vals)),3) if len(vals)==5 else None
                native=round(float(np.mean([r["totals"][i]["native_total_mm"] for r in five])),3) if len(vals)==5 else None
                previous=oldtowns.get(p["name"],{}).get("mean_precip24_mm")
                towns.append(dict(town=p["name"],five_member_mean_mm=mean,five_member_native_mean_mm=native,
                    five_member_values_mm={r["member"]:r["totals"][i]["total_mm"] for r in five},
                    published_ensemble_mean_mm=avg["totals"][i]["total_mm"] if avg else None,
                    prior_live_mean_mm=previous,
                    difference_vs_prior_mm=round(native-previous,3) if res=="0p50" and native is not None and previous is not None else None,
                    sampled_grid=[selected[0]["totals"][i]["grid_lat"],selected[0]["totals"][i]["grid_lon"]] if selected else None,
                    max_decode_difference_mm=max((r["totals"][i]["max_decode_difference_mm"] for r in selected),default=None)))
            summaries.append(dict(date=date,lead_hours=lead,resolution=res,five_members=len(five),published_mean_available=avg is not None,towns=towns))
    payload=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),summaries=summaries,errors=errors,
        note="Independent array-based nearest-grid extraction versus ecCodes nearest, exact accumulation metadata, 0.25/0.50 degree products, five historical-compatible members versus NOAA published ensemble mean. Nov25 +48h is a comparison only; no trained 48h model.")
    (OUT/"precip_audit.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
    (OUT/"precip_audit_records.json").write_text(json.dumps(results,separators=(",",":"),allow_nan=False)+"\n")
    print(json.dumps(payload,allow_nan=False),flush=True)
if __name__=="__main__":main()
