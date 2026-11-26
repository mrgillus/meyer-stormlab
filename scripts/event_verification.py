"""Capture real daily station reports and link them to forecast-event case files.

RCC-ACIS snowfall/precipitation are STATION OBSERVATIONS, but their daily
report dates are NOT necessarily midnight-to-midnight forecast windows.
This pipeline deliberately does not compute model accuracy, verification
scores, event storm totals or an observed total for a forecast town.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVENTS = DATA / "events"
OUT = DATA / "verification"
SOURCE = "https://data.rcc-acis.org/MultiStnData"
TZ = ZoneInfo("America/Chicago")
UTC = dt.timezone.utc
BBOX = (-91.35, 38.25, -89.75, 39.2)
TOWNS = {
    "Wentzville": (38.8114, -90.8529),
    "St. Louis": (38.6270, -90.1994),
    "St. Charles": (38.7881, -90.4974),
    "Affton": (38.5581, -90.3366),
    "Wildwood": (38.5920, -90.5587),
    "Washington": (38.5581, -91.0121),
    "Belleville": (38.5201, -89.9839),
    "Alton": (38.8906, -90.1843),
}
MAX_DISTANCE_MI = 20.0
MAX_CASE_DAYS = 30
MAX_STATIONS = 1000
MAX_REPORTS = 20000

def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(obj, indent=2, allow_nan=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)

def parseday(s):
    return dt.date.fromisoformat(s)

def parseutc(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)

def distance_miles(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dp = phi2 - phi1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 3958.7613 * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0., 1-h)))

def decode_amount(raw):
    """Return a finite measurement or a qualifying reason to exclude it.

    Trace, accumulated/multiday, missing, and flagged records are never
    converted to measured zero. ACIS add=t,f arrays are preserved.
    """
    value = raw
    clock = None
    flags = None
    if isinstance(raw, (list, tuple)):
        if not raw:
            return (None, "missing", None, None)
        value = raw[0]
        clock = str(raw[1]).strip() if len(raw)>1 and raw[1] is not None else None
        flags = str(raw[2]).strip() if len(raw)>2 and raw[2] is not None else None
    if flags and flags.upper() not in ("", "-"):
        return (None, "flagged", clock, flags)
    if value is None or isinstance(value, bool):
        return (None, "missing", clock, flags)
    if isinstance(value, str):
        token = value.strip().upper()
        if token in ("", "M", "NA", "NULL", "-"):
            return (None, "missing", clock, flags)
        if token == "T":
            return (None, "trace", clock, flags)
        if token.endswith("A"):
            return (None, "accumulated_interval_unknown", clock, flags)
    try:
        amount = float(value)
    except (ValueError, TypeError):
        return (None, "unrecognized", clock, flags)
    if not math.isfinite(amount) or amount < 0 or amount > 80:
        return (None, "out_of_range", clock, flags)
    return (round(amount, 3), "measured", clock, flags)

def provider_request(start, end):
    q = {
        "bbox": ",".join(str(x) for x in BBOX),
        "sdate": start.isoformat(), "edate": end.isoformat(),
        "meta": "name,state,ll,sids,uid",
        "elems": json.dumps([
            {"name":"snow", "interval":"dly", "duration":"dly", "add":"t,f"},
            {"name":"pcpn", "interval":"dly", "duration":"dly", "add":"t,f"},
        ], separators=(",",":")),
        "output": "json",
    }
    return SOURCE+"?"+urllib.parse.urlencode(q)

def fetch_observations(start,end):
    url=provider_request(start,end)
    last=None
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"MeyerStormLab/1.0 (research; regional daily station evidence)","Accept":"application/json"})
            with urllib.request.urlopen(req,timeout=45) as res:
                result=json.load(res)
            if not isinstance(result,dict) or "error" in result or not isinstance(result.get("data"),list):
                raise ValueError("ACIS did not return a valid station data array")
            return result,url
        except (urllib.error.HTTPError, urllib.error.URLError,TimeoutError,ValueError,OSError) as exc:
            last=exc
            if attempt<2:time.sleep(2*(attempt+1))
    raise RuntimeError("RCC-ACIS unavailable or did not return a valid response: "+str(last))

def normalize_provider(raw,start,end):
    stations = []
    observations = []
    seen = set()
    for entry in raw.get("data",[])[:MAX_STATIONS]:
        if not isinstance(entry,dict):continue
        meta=entry.get("meta") or {}
        ll=meta.get("ll")
        if not isinstance(ll,(list,tuple)) or len(ll)!=2:continue
        try:
            lon,lat=map(float,ll)
        except (TypeError,ValueError):continue
        if not (-92<lon<-89 and 37.5<lat<40):continue
        uid=meta.get("uid")
        sids=meta.get("sids") or []
        sid=str(uid) if uid is not None else (str(sids[0]) if sids else "")
        if not sid:continue
        identity="ACIS:"+sid
        if identity in seen:continue
        seen.add(identity)
        station={"station_id":identity,"name":str(meta.get("name") or "Unnamed station")[:100],
                 "state":str(meta.get("state") or "")[:3],"lat":round(lat,5),"lon":round(lon,5),
                 "source_ids":[str(v)[:55] for v in sids[:8]],"source":"RCC-ACIS daily station data"}
        nearest=sorted(((distance_miles((lat,lon),coords),town) for town,coords in TOWNS.items()))
        station["nearest_town"]=nearest[0][1]
        station["nearest_town_distance_mi"]=round(nearest[0][0],1)
        if nearest[0][0]>MAX_DISTANCE_MI:continue
        stations.append(station)
        rows=entry.get("data") or []
        for row in rows:
            if not isinstance(row,(list,tuple)) or len(row)<3:continue
            try:
                day=parseday(row[0])
            except (TypeError,ValueError):continue
            if day<start or day>end:continue
            snow,snow_quality,snow_clock,snow_flag=decode_amount(row[1])
            liquid,liquid_quality,liquid_clock,liquid_flag=decode_amount(row[2])
            if snow_quality=="missing" and liquid_quality=="missing":continue
            observations.append({
                "station_id":identity,"reported_date":day.isoformat(),
                "snowfall_in":snow,"snow_quality":snow_quality,
                "precipitation_in":liquid,"precip_quality":liquid_quality,
                "snow_observation_clock_raw":snow_clock,
                "precip_observation_clock_raw":liquid_clock,
                "snow_flag":snow_flag,"precip_flag":liquid_flag,
                "observation_interval_verified":False,
                "note":"ACIS reported date is not verified as a local midnight-to-midnight interval.",
            })
            if len(observations)>=MAX_REPORTS:break
        if len(observations)>=MAX_REPORTS:break
    return stations, observations

def find_prior_capture(case):
    """Find a frozen archived forecast capture made before event day 1.

    Preserves source issue/retrieval times; never uses after-the-fact guidance.
    """
    day=parseday(case["start_date"])
    cutoff=dt.datetime.combine(day,dt.time.min,tzinfo=TZ).astimezone(UTC)
    candidates=[]
    for record in case.get("timeline",[]):
        try:
            stamp=parseutc(record["captured_at"])
            if stamp<cutoff:candidates.append(stamp)
        except (KeyError,ValueError,TypeError):pass
    if not candidates:return None
    stamp=max(candidates)
    month=stamp.strftime("%Y-%m")
    name=stamp.strftime("%Y%m%dT%H%M%SZ")+".json"
    path=DATA/"snapshots"/month/name
    if not path.is_file():return None
    return stamp,path

def forecast_reference(case):
    found=find_prior_capture(case)
    if not found:return {"status":"no_prior_capture","daily_forecasts":[],
                          "note":"No frozen pre-event model capture was found; none will be reconstructed."}
    captured,path=found
    snap=load(path,{})
    by_place=snap.get("locations") or {}
    end=parseday(case["end_date"])
    start=parseday(case["start_date"])
    if (end-start).days>MAX_CASE_DAYS:
        return {"status":"unsupported_span","daily_forecasts":[]}
    forecasts=[]
    for town,models in by_place.items():
        if town not in TOWNS or not isinstance(models,dict):continue
        for model,info in models.items():
            if model not in ("GFS","ECMWF","GEM","ICON"):continue
            daily=info.get("daily") or {}
            dates=daily.get("time") or []
            unit=info.get("units",{}).get("snowfall_sum")
            for i,day in enumerate(dates):
                if day<start.isoformat() or day>end.isoformat():continue
                snowfall=daily.get("snowfall_sum") or []
                value=snowfall[i] if i<len(snowfall) else None
                if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:continue
                if unit=="cm":value/=2.54
                elif unit not in ("in","inch"):continue
                forecasts.append({"town":town,"model":model,"calendar_date":day,"modeled_daily_snow_in":round(value,3)})
    return {"status":"pre_event_capture","captured_at":captured.isoformat(),
            "snapshot_path":str(path.relative_to(ROOT)),
            "daily_forecasts":forecasts[:500],
            "note":"Model daily calendar windows have NOT been matched to station observation-day windows. Do not calculate an error score."}

def eligible_cases(catalog,today):
    result=[]
    for case in catalog.get("events",[]):
        try:
            start=parseday(case["start_date"])
            end=parseday(case["end_date"])
        except (TypeError,KeyError,ValueError):continue
        if start>end or (end-start).days>MAX_CASE_DAYS:continue
        if end>=today-dt.timedelta(days=21) and start<=today+dt.timedelta(days=1):
            result.append(case)
    return result[:30]

def build_reports(cases,stations,observations,today):
    by_id={s["station_id"]:s for s in stations}
    out=[]
    for case in cases:
        start=parseday(case["start_date"])
        end=parseday(case["end_date"])
        category={"snow":"snowfall_in","precipitation":"precipitation_in"}.get(case["kind"])
        earliest=start-dt.timedelta(days=1)
        latest=end+dt.timedelta(days=2)
        suitable=[o for o in observations if
                  earliest<=parseday(o["reported_date"])<=latest and
                  category and o.get(category) is not None and
                  o.get("snow_quality" if category=="snowfall_in" else "precip_quality")=="measured" and
                  o["station_id"] in by_id]
        # Prefer recently reported and close stations; these are station daily reports, not storm totals.
        suitable.sort(key=lambda o:(
            by_id[o["station_id"]]["nearest_town"]!="Wentzville",
            by_id[o["station_id"]]["nearest_town_distance_mi"],
            o["reported_date"],o["station_id"]))
        rows=[]
        for o in suitable[:45]:
            station=by_id[o["station_id"]]
            rows.append({
                "station_id":o["station_id"],"station_name":station["name"],
                "near_town":station["nearest_town"],
                "distance_to_near_town_mi":station["nearest_town_distance_mi"],
                "reported_date":o["reported_date"],
                "value_in":o[category],
                "quantity":"daily snowfall" if category=="snowfall_in" else "daily liquid precipitation",
                "raw_observation_clock":o["snow_observation_clock_raw"] if category=="snowfall_in" else o["precip_observation_clock_raw"],
                "measurement_interval_verified":False
            })
        ref=forecast_reference(case) if category else {"status":"unsupported_variable","daily_forecasts":[]}
        reason=("Daily station observation windows have not been verified against model calendar-day windows."
                if category and rows else
                "No compatible measured daily station reports are available yet." if category else
                "No supported daily station measurement for this signal type.")
        status=("observations_available_timing_review_required" if rows
                else "awaiting_observations" if category else "measurement_type_not_connected")
        if end>=today:
            status="event_window_not_complete" if category else status
        out.append({
            "event_id":case["id"],"kind":case["kind"],"title":case["title"],
            "forecast_dates":{"start":case["start_date"],"end":case["end_date"]},
            "observation_status":status,
            "verification_status":"not_scored",
            "verification_reason":reason if end<today else "Forecast window not yet over; "+reason,
            "actual_observed_storm_total_in":None,
            "forecast_error_in":None,"model_skill":None,
            "station_daily_reports":rows,
            "station_count":len({r["station_id"] for r in suitable}),
            "daily_report_count":len(suitable),
            "forecast_reference":ref,
            "safety_note":"Stations report daily measurements, NOT an observed 48-hour storm total. Dates and locations may not align with model forecasts."
        })
    return out

def main():
    now=dt.datetime.now(UTC)
    today=now.astimezone(TZ).date()
    catalog=load(EVENTS/"catalog.json",{})
    cases=eligible_cases(catalog,today)
    prior=load(OUT/"observations.json",{})
    station_data=[]
    obs=[]
    provider_state="not_requested"
    retrieval_time=None
    provider_error=None
    url=None
    if cases:
        start=max(today-dt.timedelta(days=23), min(parseday(c["start_date"]) for c in cases)-dt.timedelta(days=2))
        end=min(today, max(parseday(c["end_date"]) for c in cases)+dt.timedelta(days=2))
        try:
            provider,url=fetch_observations(start,end)
            station_data,obs=normalize_provider(provider,start,end)
            provider_state="retrieved"
            retrieval_time=now.isoformat()
        except Exception as exc:
            provider_error=str(exc)[:300]
            provider_state="unavailable"
            if prior.get("schema")==1:
                station_data=prior.get("stations",[])
                obs=prior.get("reports",[])
                retrieval_time=prior.get("retrieved_at")
                provider_state="cached_previous_capture"
    payload={
        "schema":1,"generated_at":now.isoformat(),
        "retrieved_at":retrieval_time,"provider":"RCC-ACIS MultiStnData",
        "provider_endpoint":SOURCE,"provider_status":provider_state,
        "provider_error":provider_error,
        "stations":station_data,"reports":obs,
        "limitations":["Station daily reporting window may not match calendar day.",
            "Trace and accumulated/multiday values are not numeric zero.",
            "Station reports are not ground truth at nearby towns without geospatial context.",
            "No total snowfall from a daily reported date without interval harmonization.",
            "Independent ACIS station observations are never overwritten by modeled forecasts."],
    }
    save(OUT/"observations.json",payload)
    summaries=build_reports(cases,station_data,obs,today)
    report={
        "schema":1,"generated_at":now.isoformat(),
        "source_latest_observation_capture":retrieval_time,
        "provider_status":provider_state,"provider_error":provider_error,
        "source":"RCC-ACIS daily station reports (snowfall and liquid precipitation)",
        "observed_station_count":len(station_data),
        "reported_station_days":len(obs),
        "cases_reviewed":len(summaries),
        "cases_with_daily_reports":sum(x["daily_report_count"]>0 for x in summaries),
        "cases_scored":0,
        "verification_policy":"fail_closed_until_observation_interval_and_station_location_are_aligned",
        "events":summaries,
    }
    save(OUT/"report.json",report)
    print("Verification: %s stations, %s station-days, %s cases, %s verified scores"
          %(len(station_data),len(obs),len(summaries),0))

if __name__=="__main__":
    main()
