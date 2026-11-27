"""Active winter event: matched valid-hour, multi-family experimental guidance.
The window comes from the named-event catalog (scripts/event_windows.py), not
hardcoded dates. No learned weights, no ground snow-depth or calibrated
probability claims.
"""
import datetime as dt
import json
import math
import pathlib
import statistics as st
import urllib.parse
import urllib.request
import urllib.error
import time
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
import event_windows

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"storm"
TZ=ZoneInfo("America/Chicago")
# Set per run from the active event window (see configure()).
START=END=None
HOURS=[]
WINDOW=None
def configure(window):
    """Point the module at one event window. Hour stamps mark hour ends."""
    global START,END,HOURS,WINDOW
    START=dt.datetime.fromisoformat(window["start"]).astimezone(TZ)
    END=dt.datetime.fromisoformat(window["end"]).astimezone(TZ)
    n=int((END-START).total_seconds()//3600)
    if n<1:raise ValueError("Empty event window")
    HOURS=[int((START+dt.timedelta(hours=i)).timestamp()) for i in range(1,n+1)]
    WINDOW=window
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
def fetch(model,now):
    q=urllib.parse.urlencode(dict(latitude=",".join(str(p["lat"]) for p in POINTS),
        longitude=",".join(str(p["lon"]) for p in POINTS),models=model,
        hourly=",".join(FIELDS),timezone="GMT",timeformat="unixtime",
        start_date=max(START.astimezone(dt.timezone.utc).date(),now.date()).isoformat(),
        end_date=END.astimezone(dt.timezone.utc).date().isoformat(),cell_selection="nearest"))
    req=urllib.request.Request("https://api.open-meteo.com/v1/forecast?"+q,headers={"User-Agent":"MeyerStormLab experimental noncommercial"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=45) as response: data=json.load(response)
            if not isinstance(data,list) or len(data)!=len(POINTS):raise ValueError("Unexpected location count")
            return data
        except urllib.error.HTTPError as e:
            body=e.read(600).decode(errors="replace")
            if attempt==2 or e.code not in (429,500,502,503,504):
                raise RuntimeError(f"HTTP {e.code}: {body}") from e
        except (OSError,TimeoutError) as e:
            if attempt==2:raise
        time.sleep(3*(attempt+1))
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
def summarize(p,series,now,prior):
    # Never reinterpret elapsed forecast hours as observations.
    cutoff=int(now.timestamp())
    series={n:{t:x for t,x in v.items() if t>cutoff} for n,v in series.items()}
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
    previous={}
    if prior:
        for x in prior.get("hourly",[]):
            try:previous[int(dt.datetime.fromisoformat(x["end_ct"]).timestamp())]=x
            except (KeyError,ValueError,TypeError):continue
    future_expected=sum(t>cutoff for t in HOURS)
    for ts in HOURS:
        if ts<=cutoff:
            x=previous.get(ts)
            if x is not None and finite(x.get("snow_in")) and x["snow_in"]>=0:
                hourly.append(dict(x,source="prior_forecast",source_retrieved_at=x.get("source_retrieved_at") or prior.get("_capture")))
            continue
        families={}
        for n,v in series.items():
            if ts in v:families.setdefault(MODELS[n][1],[]).append(v[ts]["snow"])
        # Require two global members; short-range partial coverage supplements, never truncates, totals.
        if len(families.get("global",[]))<2:continue
        weights={k:BASE_WEIGHTS[k] for k in families}
        total=sum(weights.values())
        val=sum(st.mean(families[k])*w/total for k,w in weights.items())
        hourly.append({"end_ct":dt.datetime.fromtimestamp(ts,TZ).isoformat(),"snow_in":round(val,4),
            "families":{k:len(v) for k,v in families.items()},"rapid_included":"rapid" in families,"nbm_included":"blend" in families,
            "source":"current_forecast","source_retrieved_at":now.isoformat()})
    fresh=[v for v in hourly if v["source"]=="current_forecast"]
    frozen=[v for v in hourly if v["source"]=="prior_forecast"]
    full=[v["full_total_in"] for v in models.values() if v["full_total_in"] is not None]
    return dict(**p,models=models,blend_in=round(sum(v["snow_in"] for v in hourly),2) if len(hourly)==len(HOURS) else None,
        blend_hours=len(hourly),future_hours=len(fresh),future_expected_hours=future_expected,
        remaining_forecast_in=round(sum(x["snow_in"] for x in fresh),2) if len(fresh)==future_expected else None,
        elapsed_forecast_hours=len(frozen),elapsed_forecast_in=round(sum(x["snow_in"] for x in frozen),2) if len(frozen)==len(HOURS)-future_expected else None,
        rapid_hours=sum(x["rapid_included"] for x in hourly),
        nbm_hours=sum(x["nbm_included"] for x in hourly),full_model_min_in=min(full) if full else None,
        full_model_max_in=max(full) if full else None,hourly=hourly)
def main():
    now=dt.datetime.now(dt.timezone.utc)
    window=event_windows.current(now,persist=True)
    if window is None:
        print("No active named winter event window; no forecast overwritten.");return
    configure(window)
    print("Event window",window["event_name"],window["event_id"],START.isoformat(),"to",END.isoformat(),len(HOURS),"hours",flush=True)
    data={};errors=[];prior={}
    try:
        old=json.loads((OUT/"report.json").read_text())
        # Start is pinned for a window; the end may extend. Frozen hours are keyed by timestamp.
        # Schema-2 reports predate event ids; their pinned start still identifies the window.
        if old.get("window_start")==START.isoformat() and old.get("event_id",window["event_id"])==window["event_id"]:
            prior={p["name"]:dict(p,_capture=old["retrieved_at"]) for p in old["points"]}
    except (OSError,ValueError,KeyError,TypeError):pass
    with ThreadPoolExecutor(max_workers=2) as ex:
        for n,f in [(n,ex.submit(fetch,m[0],now)) for n,m in MODELS.items()]:
            try:data[n]=f.result()
            except Exception as e:
                errors.append(dict(model=n,error=str(e)))
                print("Retrieval error:",n,str(e),flush=True)
    points=[]
    for i,p in enumerate(POINTS):
        series={}
        for n,rows in data.items():
            try:series[n]=extract(rows[i],p)
            except Exception as e:errors.append(dict(model=n,point=p["name"],error=str(e)))
        old=prior.get(p["name"])
        if old and (old.get("lat")!=p["lat"] or old.get("lon")!=p["lon"]):old=None
        points.append(summarize(p,series,now,old))
    result=dict(schema=3,retrieved_at=now.isoformat(),window_start=START.isoformat(),window_end=END.isoformat(),
        event_id=window["event_id"],event_name=window["event_name"],event_kinds=window["kinds"],event_mode_ids=window["mode_ids"],
        hours=len(HOURS),weights=BASE_WEIGHTS,points=points,errors=errors,
        note="Experimental snowfall guidance, not observed snowfall or ground depth. Elapsed hours are frozen prior forecasts, not measurements; per-model totals cover future hours only and are partial after storm start. Storm total combines frozen prior estimates and current future guidance. Missing future hours are not filled with zeros or stale estimates. Family weights are heuristic, NBM overlaps other models, provider may stitch cycles.")
    OUT.mkdir(parents=True,exist_ok=True)
    complete=sum(p["blend_in"] is not None for p in points)
    status=dict(retrieved_at=now.isoformat(),event_id=window["event_id"],event_name=window["event_name"],
        window_start=START.isoformat(),window_end=END.isoformat(),status="published" if complete==len(POINTS) else "incomplete_retained_previous",
        complete_points=complete,total_points=len(POINTS),errors=errors,
        points=[{k:p[k] for k in ("name","blend_hours","future_hours","future_expected_hours","elapsed_forecast_hours","remaining_forecast_in")} for p in points])
    (OUT/"status.json").write_text(json.dumps(status,indent=2,allow_nan=False)+"\n")
    print(json.dumps(status,indent=2),flush=True)
    if complete!=len(POINTS):
        print("::warning::Incomplete regional coverage; retaining previous complete report and publishing diagnostics.",flush=True)
        return
    stamp=now.strftime("%Y%m%dT%H%M%SZ")
    (OUT/"captures").mkdir(exist_ok=True)
    content=json.dumps(result,separators=(",",":"),allow_nan=False)+"\n"
    (OUT/"captures"/(stamp+".json")).write_text(content)
    (OUT/"report.json").write_text(content)
    print(json.dumps({"retrieved_at":result["retrieved_at"],"towns":[{k:v for k,v in p.items() if k!="hourly"} for p in points if p["kind"]=="town"],"errors":errors[:20]},indent=2),flush=True)
if __name__=="__main__":main()
