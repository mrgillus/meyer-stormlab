"""Experimental station snowfall hindcast. Fixed hourly lead offsets, NOT single-run daily forecasts."""
import csv
import datetime as dt
import json
import math
import pathlib
import statistics as st
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "training"
STATION = "USW00013994"
LAT, LON = 38.7525, -90.3736
TZ = ZoneInfo("America/Chicago")
LEADS = (1, 3, 5, 7)
MODELS = {"GFS":"ncep_gfs_global","ECMWF":"ecmwf_ifs025","GEM":"cmc_gem_gdps","ICON":"icon_global"}
VARIABLES = ("snowfall","temperature_2m","precipitation","pressure_msl")
PERIODS = (("2024-01-01","2024-03-31"),("2024-10-01","2024-12-31"),
           ("2025-01-01","2025-03-31"),("2025-10-01","2025-12-31"),
           ("2026-01-01","2026-03-31"))
TEST_START = "2025-10-01"

def get(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url,headers={"User-Agent":"MeyerStormLab/0.3 noncommercial weather research"})
            with urllib.request.urlopen(req,timeout=60) as r:
                return r.read()
        except (OSError,ValueError) as e:
            if attempt == 2: raise
            time.sleep(2 ** attempt)

def observations():
    # GHCN fixed width: SNOW is mm, reject missing and quality-flagged values.
    text = get("https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/"+STATION+".dly").decode()
    obs = {}
    for line in text.splitlines():
        if line[17:21] != "SNOW": continue
        year,month = int(line[11:15]),int(line[15:17])
        if year not in (2024,2025,2026): continue
        for i in range(31):
            chunk = line[21+8*i:29+8*i]
            if len(chunk)!=8: continue
            try:
                day = dt.date(year,month,i+1).isoformat()
                val = int(chunk[:5])
            except ValueError: continue
            # P means missing presumed zero, exclude rather than assume. T is trace.
            if val >= 0 and chunk[6] == " " and chunk[5] not in ("P","A"):
                obs[day] = val / 25.4
    if not obs: raise RuntimeError("No quality-controlled station snowfall observations")
    return obs

def forecast(model,start,end):
    # UTC retrieval avoids ambiguous DST hours; aggregate hourly preceding-hour amounts
    # to local calendar day by assigning an hour ending at midnight to preceding day.
    start = (dt.date.fromisoformat(start)-dt.timedelta(days=1)).isoformat()
    end = (dt.date.fromisoformat(end)+dt.timedelta(days=1)).isoformat()
    hourly = [f"{v}_previous_day{d}" for d in LEADS for v in VARIABLES]
    params = dict(latitude=LAT,longitude=LON,models=model,hourly=",".join(hourly),
                  start_date=start,end_date=end,timezone="GMT")
    url = "https://previous-runs-api.open-meteo.com/v1/forecast?"+urllib.parse.urlencode(params)
    raw = json.loads(get(url))
    h,u = raw["hourly"],raw["hourly_units"]
    buckets = defaultdict(list)
    for i,t in enumerate(h["time"]):
        ts = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        local = (ts-dt.timedelta(microseconds=1)).astimezone(TZ)
        buckets[local.date().isoformat()].append(i)
    result = {}
    for day,ix in buckets.items():
        date=dt.date.fromisoformat(day)
        a=dt.datetime.combine(date,dt.time(),TZ).astimezone(dt.timezone.utc)
        b=dt.datetime.combine(date+dt.timedelta(days=1),dt.time(),TZ).astimezone(dt.timezone.utc)
        if len(ix)!=int((b-a).total_seconds()/3600): continue
        for lead in LEADS:
            keys=[f"{v}_previous_day{lead}" for v in VARIABLES]
            if not all(k in h and len(h[k])==len(h["time"]) for k in keys): continue
            cols=[[h[k][i] for i in ix] for k in keys]
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for c in cols for v in c): continue
            snowunit=u.get(keys[0]); punit=u.get(keys[2])
            if snowunit not in ("cm","inch","in","mm") or punit not in ("mm","inch","in") or u.get(keys[1])!="°C" or u.get(keys[3])!="hPa":
                raise ValueError("Unexpected units "+str(u))
            snow=max(0,sum(cols[0])) / {"cm":2.54,"mm":25.4,"inch":1,"in":1}[snowunit]
            precip=max(0,sum(cols[2])) / (25.4 if punit=="mm" else 1)
            result[(day,lead)] = [snow,min(cols[1]),max(cols[1]),precip,st.mean(cols[3])]
    return result

def scores(y,p):
    n=len(y); snow=[i for i,v in enumerate(y) if v>=1]
    hits=sum(a>=1 and b>=1 for a,b in zip(y,p))
    misses=sum(a>=1 and b<1 for a,b in zip(y,p))
    false=sum(a<1 and b>=1 for a,b in zip(y,p))
    return {"mae":round(st.mean(abs(a-b) for a,b in zip(y,p)),4),
            "rmse":round(math.sqrt(st.mean((a-b)**2 for a,b in zip(y,p))),4),
            "bias":round(st.mean(b-a for a,b in zip(y,p)),4),
            "snow_day_mae":round(st.mean(abs(y[i]-p[i]) for i in snow),4) if snow else None,
            "hits_1in":hits,"misses_1in":misses,"false_alarms_1in":false,"n":n}

def features(row,names):
    vals=[row["models"][m] for m in names]
    snow=[v[0] for v in vals]
    return [*snow,st.mean(snow),max(snow)-min(snow),
            *[st.mean(v[k] for v in vals) for k in (1,2,3,4)]]

def train_eval(rows,lead):
    from sklearn.ensemble import RandomForestRegressor
    train_all=[r for r in rows if r["lead"]==lead and r["date"]<TEST_START]
    test_all=[r for r in rows if r["lead"]==lead and r["date"]>=TEST_START]
    # Choose coverage using training only. Same complete cases for all competing forecasts.
    names=[m for m in MODELS if sum(m in r["models"] for r in train_all)>=0.8*len(train_all)]
    tr=[r for r in train_all if all(m in r["models"] for m in names)]
    te=[r for r in test_all if all(m in r["models"] for m in names)]
    if len(names)<2 or len(tr)<100 or len(te)<60 or sum(r["actual"]>=1 for r in tr)<5:
        return {"lead":lead,"status":"insufficient_data","models":names,"train_days":len(tr),"test_days":len(te),"train_snow_days":sum(r["actual"]>=1 for r in tr)}
    x=[features(r,names) for r in tr]; y=[r["actual"] for r in tr]
    # Fixed hyperparameters; no tuning against holdout. Shallow forest regularizes small dataset.
    model=RandomForestRegressor(n_estimators=200,max_depth=4,min_samples_leaf=5,random_state=42,n_jobs=2)
    model.fit(x,y)
    pred=[max(0,float(v)) for v in model.predict([features(r,names) for r in te])]
    actual=[r["actual"] for r in te]
    comparisons={"StormLab RF":pred,"Equal blend":[st.mean(r["models"][m][0] for m in names) for r in te],
                 "No snow":[0]*len(te)}
    for m in names: comparisons[m]=[r["models"][m][0] for r in te]
    report={"lead":lead,"status":"evaluated","models":names,"train_days":len(tr),"test_days":len(te),
            "train_snow_days":sum(v>=1 for v in y),"test_snow_days":sum(v>=1 for v in actual),
            "test_days_available":len(test_all),"metrics":{k:scores(actual,v) for k,v in comparisons.items()},
            "events":[{"date":r["date"],"actual":round(r["actual"],2),
                       "predictions":{k:round(v[i],2) for k,v in comparisons.items()}}
                      for i,r in enumerate(te) if r["actual"]>=1 or any(v[i]>=1 for k,v in comparisons.items())]}
    return report

def main():
    obs=observations(); rows=[]; errors=[]; forecasts={}
    for name,model in MODELS.items():
        forecasts[name]={}
        for start,end in PERIODS:
            try:
                data=forecast(model,start,end)
                forecasts[name].update({k:v for k,v in data.items() if start<=k[0]<=end})
                print(name,start,end,len(data),flush=True)
            except Exception as e:
                errors.append({"model":name,"start":start,"error":str(e)})
                print(errors[-1],flush=True)
    for day,actual in sorted(obs.items()):
        if not any(a<=day<=b for a,b in PERIODS): continue
        for lead in LEADS:
            rows.append({"date":day,"lead":lead,"actual":actual,
                         "models":{m:data[(day,lead)] for m,data in forecasts.items() if (day,lead) in data}})
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/"hindcasts.csv").open("w",newline="") as f:
        w=csv.writer(f);w.writerow(["valid_date","lead_offset_days","observed_snow_in"]+
          [f"{m}_{v}" for m in MODELS for v in ("snow_in","min_c","max_c","precip_in","pressure_hpa")])
        for r in rows:
            w.writerow([r["date"],r["lead"],round(r["actual"],4)]+
                       [round(v,4) if v is not None else "" for m in MODELS for v in r["models"].get(m,[None]*5)])
    report={"schema":1,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),
            "station":STATION,"station_name":"St Louis Lambert International Airport",
            "train_period":"Jan–Mar 2024 and Oct 2024–Mar 2025",
            "test_period":"Oct 2025–Mar 2026","observed_days":len({r["date"] for r in rows}),
            "method":"Fixed hourly lead-offset guidance aggregated to local days; experimental random forest; no tuning on holdout.",
            "limitations":["Station reporting day may differ from local calendar day.","Daily totals use hourly lead offsets, not a forecast issued at a single time.","Only one station and few winters; no calibrated probabilities, regional forecast or operational model yet.","Snowfall variables may include assumed snow-to-liquid ratios; trace treated as zero."],
            "errors":errors,"results":[train_eval(rows,l) for l in LEADS]}
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="results"},ensure_ascii=False),flush=True)
    for r in report["results"]: print(r["lead"],r["status"],r.get("metrics",{}),flush=True)
if __name__=="__main__": main()
