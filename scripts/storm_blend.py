"""Thanksgiving storm: matched valid-hour, multi-family experimental guidance.
No learned weights, no ground snow-depth or calibrated probability claims.
"""
import datetime as dt
import json
import math
import pathlib
import statistics as st
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"storm"
TZ=ZoneInfo("America/Chicago")
START=dt.datetime(2026,11,25,0,tzinfo=TZ)
END=dt.datetime(2026,11,27,0,tzinfo=TZ)
HOURS=[int((START+dt.timedelta(hours=i)).timestamp()) for i in range(1,49)]
MODELS={"GFS":("ncep_gfs_global","global"),"ECMWF":("ecmwf_ifs025","global"),
"GEM":("cmc_gem_gdps","global"),"ICON":("icon_global","global"),
"HRRR":("ncep_hrrr_conus","rapid"),"RRFS":("ncep_rrfs_conus","rapid"),
"NBM":("ncep_nbm_conus","blend")}
POINTS=[dict(name=n,lat=a,lon=b,kind="town") for n,a,b in [
("Wentzville",38.8114,-90.8529),("Florissant",38.7892,-90.3226),
("Troy",38.9795,-90.9807),("Hawk Point",38.9709,-91.1335),
("Lake St. Louis",38.7976,-90.7857)]]
POINTS += [dict(name=f"grid-{y}-{x}",lat=round(38.65+y*.12,4),lon=round(-91.25+x*.16,4),kind="grid")
for y in range(5) for x in range(7)]
FIELDS=("snowfall","temperature_2m","precipitation","wet_bulb_temperature_2m")
BASE_WEIGHTS={"global":.4,"rapid":.4,"blend":.2}
def fetch(model):
    q=urllib.parse.urlencode(dict(latitude=",".join(str(p["lat"]) for p in POINTS),
        longitude=",".join(str(p["lon"]) for p in POINTS),models=model,
        hourly=",".join(FIELDS),timezone="GMT",timeformat="unixtime",
        start_date="2026-11-25",end_date="2026-11-27",cell_selection="nearest"))
    req=urllib.request.Request("https://api.open-meteo.com/v1/forecast?"+q,headers={"User-Agent":"MeyerStormLab experimental noncommercial"})
    with urllib.request.urlopen(req,timeout=75) as response: data=json.load(response)
    if not isinstance(data,list) or len(data)!=len(POINTS):raise ValueError("Unexpected location count")
    return data
def finite(v):return isinstance(v,(int,float)) and math.isfinite(v)
def extract(raw,p):
    h=raw["hourly"];u=raw["hourly_units"]; idx={t:i for i,t in enumerate(h["time"])}
    if abs(raw["latitude"]-p["lat"])>.4 or abs(raw["longitude"]-p["lon"])>.4:raise ValueError("Unexpected grid position")
    if u.get("snowfall") not in ("cm","inch","in","mm"):raise ValueError("Unrecognized snowfall unit")
    conv={"cm":1/2.54,"mm":1/25.4,"inch":1,"in":1}[u["snowfall"]]
    values={}
    for ts in HOURS:
        i=idx.get(ts)
        if i is None:continue
        def get(k):
            a=h.get(k,[])
            return a[i] if i<len(a) and finite(a[i]) else None
        s=get("snowfall")
        if s is None or s<0:continue
        temp=get("temperature_2m"); wet=get("wet_bulb_temperature_2m"); pr=get("precipitation")
        values[ts]={"snow":s*conv,"temp_c":temp if u.get("temperature_2m")=="°C" else None,
           "wet_c":wet if u.get("wet_bulb_temperature_2m")=="°C" else None,
           "precip_mm":pr if u.get("precipitation")=="mm" and pr is not None and pr>=0 else None}
    return values
def summarize(p,series):
    models={}
    for n,v in series.items():
        snow=[x["snow"] for x in v.values()]
        snow_hours=[x for x in v.values() if x["snow"]>.01]
        wet=[x["wet_c"] for x in snow_hours if x["wet_c"] is not None]
        temp=[x["temp_c"] for x in snow_hours if x["temp_c"] is not None]
        models[n]={"hours":len(v),"full_total_in":round(sum(snow),3) if len(v)==len(HOURS) else None,
            "covered_total_in":round(sum(snow),3),"peak_hour_in":round(max(snow),3) if snow else None,
            "snow_hour_mean_temp_c":round(st.mean(temp),2) if temp else None,
            "snow_hour_mean_wetbulb_c":round(st.mean(wet),2) if wet else None,
            "last_valid_hour":dt.datetime.fromtimestamp(max(v),TZ).isoformat() if v else None}
    hourly=[]
    for ts in HOURS:
        families={}
        for n,v in series.items():
            if ts in v:families.setdefault(MODELS[n][1],[]).append(v[ts]["snow"])
        # Require two global members; short-range partial coverage supplements, never truncates, totals.
        if len(families.get("global",[]))<2:continue
        weights={k:BASE_WEIGHTS[k] for k in families}
        total=sum(weights.values())
        val=sum(st.mean(families[k])*w/total for k,w in weights.items())
        hourly.append({"end_ct":dt.datetime.fromtimestamp(ts,TZ).isoformat(),"snow_in":round(val,4),
            "families":{k:len(v) for k,v in families.items()},"rapid_included":"rapid" in families,"nbm_included":"blend" in families})
    full=[v["full_total_in"] for v in models.values() if v["full_total_in"] is not None]
    return dict(**p,models=models,blend_in=round(sum(v["snow_in"] for v in hourly),2) if len(hourly)==len(HOURS) else None,
        blend_hours=len(hourly),rapid_hours=sum(x["rapid_included"] for x in hourly),
        nbm_hours=sum(x["nbm_included"] for x in hourly),full_model_min_in=min(full) if full else None,
        full_model_max_in=max(full) if full else None,hourly=hourly)
def main():
    now=dt.datetime.now(dt.timezone.utc)
    if now>=END.astimezone(dt.timezone.utc):
        print("Storm window ended; no forecast overwritten.");return
    data={};errors=[]
    with ThreadPoolExecutor(max_workers=3) as ex:
        for n,f in [(n,ex.submit(fetch,m[0])) for n,m in MODELS.items()]:
            try:data[n]=f.result()
            except Exception as e:errors.append(dict(model=n,error=str(e)))
    points=[]
    for i,p in enumerate(POINTS):
        series={}
        for n,rows in data.items():
            try:series[n]=extract(rows[i],p)
            except Exception as e:errors.append(dict(model=n,point=p["name"],error=str(e)))
        points.append(summarize(p,series))
    result=dict(schema=1,retrieved_at=now.isoformat(),window_start=START.isoformat(),window_end=END.isoformat(),
        hours=len(HOURS),weights=BASE_WEIGHTS,points=points,errors=errors,
        note="Experimental hourly snowfall guidance, not a learned or verified ground accumulation forecast. Family weights are heuristic; NBM overlaps other models. Short-range missing hours are omitted and weights renormalized. Provider series may stitch model cycles. After storm onset, past hours in this window are model data, not observations. REFS not included pending ensemble validation.")
    if not any(p["blend_in"] is not None for p in points):raise RuntimeError("No complete blended point; previous forecast retained")
    OUT.mkdir(parents=True,exist_ok=True)
    stamp=now.strftime("%Y%m%dT%H%M%SZ")
    (OUT/"captures").mkdir(exist_ok=True)
    content=json.dumps(result,separators=(",",":"),allow_nan=False)+"\n"
    (OUT/"captures"/(stamp+".json")).write_text(content)
    (OUT/"report.json").write_text(content)
    print(json.dumps({"retrieved_at":result["retrieved_at"],"towns":[{k:v for k,v in p.items() if k!="hourly"} for p in points if p["kind"]=="town"],"errors":errors[:20]},indent=2),flush=True)
if __name__=="__main__":main()
