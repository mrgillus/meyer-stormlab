"""Recover snowfall-specific clock candidates, never verified UTC intervals."""
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OBS=ROOT/"data/regional/observations.csv.gz"
OUT=ROOT/"data/observation_clocks/acis"
SOURCE="https://data.rcc-acis.org/StnData"

def fetch(station,first,last):
    params={"sid":station+" 6","sdate":first,"edate":last,
            "meta":"name,sids,uid,ll",
            "elems":[{"name":"snow","interval":"dly","duration":"dly","add":"t,f"}]}
    body=urllib.parse.urlencode({"params":json.dumps(params)}).encode()
    for attempt in range(3):
        try:
            req=urllib.request.Request(SOURCE,data=body,headers={"User-Agent":"MeyerStormLab/1.0","Content-Type":"application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req,timeout=60) as response:
                obj=json.load(response)
            if not isinstance(obj,dict) or obj.get("error") or not isinstance(obj.get("data"),list):
                raise ValueError("Invalid ACIS response")
            return obj
        except (OSError,ValueError):
            if attempt==2:raise
            time.sleep(2**attempt)

def clock(raw):
    s=str(raw).strip()
    if re.fullmatch(r"\d{1,2}",s) and 1<=int(s)<=24:
        return f"{int(s):02d}00"
    if re.fullmatch(r"\d{4}",s) and (s=="2400" or (int(s[:2])<24 and int(s[2:])<60 and s!="0000")):
        return s
    return None

def compare(station,expected,raw):
    aliases={str(s).split()[0] for s in (raw.get("meta") or {}).get("sids",[]) if str(s).strip()}
    if station not in aliases:raise ValueError("Station identity not confirmed")
    bydate=defaultdict(list)
    for row in raw["data"]:
        if isinstance(row,list) and len(row)==2 and isinstance(row[0],str):bydate[row[0]].append(row[1])
    counts=Counter();hist=Counter();evidence=[]
    for day,(mm,mflag,sflag) in sorted(expected.items()):
        values=bydate.get(day,[])
        if len(values)!=1:counts["missing_or_duplicate"]+=1;continue
        row=values[0]
        if not isinstance(row,list) or len(row)!=3:counts["invalid_shape"]+=1;continue
        # ACIS add=t,f returns value, observation time, flags.
        value,t,flag=row
        if str(flag or "").strip() not in ("","-"):counts["flagged"]+=1;continue
        if mflag.upper()=="T" or str(value).upper()=="T":counts["trace_excluded"]+=1;continue
        try:
            if isinstance(value,bool):raise ValueError()
            inches=float(value)
            if not math.isfinite(inches) or inches<0:raise ValueError()
        except (TypeError,ValueError):counts["invalid_amount"]+=1;continue
        if abs(inches*25.4-mm)>1.77 or (inches==0)!=(mm==0):counts["amount_mismatch"]+=1;continue
        counts["amount_agrees"]+=1
        candidate=clock(t)
        if candidate:counts["clock_candidate"]+=1;hist[candidate]+=1
        else:counts["clock_unknown"]+=1
        evidence.append(dict(station=station,reported_date=day,ghcn_snow_mm=mm,
            ghcn_measurement_flag=mflag,ghcn_source_flag=sflag,acis_snow_in=inches,
            acis_clock_raw=t,candidate_clock=candidate,observation_interval_verified=False))
    return dict(station=station,expected=len(expected),counts=dict(counts),clock_histogram=dict(hist)),evidence

def main():
    expected=defaultdict(dict)
    with gzip.open(OBS,"rt") as stream:
        for r in csv.DictReader(stream):
            s,d=r["station"],r["reported_date"]
            if d in expected[s]:raise ValueError("Duplicate GHCN date")
            expected[s][d]=(int(r["snow_mm"]),r["measurement_flag"].strip(),r["source_flag"].strip())
    OUT.mkdir(parents=True,exist_ok=True)
    def run(s,d):return compare(s,d,fetch(s,min(d),max(d)))
    stations=[];rows=[];errors=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for s,f in [(s,pool.submit(run,s,d)) for s,d in sorted(expected.items())]:
            try:
                report,evidence=f.result();stations.append(report);rows.extend(evidence)
                print(s,report["counts"],flush=True)
            except Exception as exc:errors.append(dict(station=s,error=str(exc)))
    if not stations:raise RuntimeError("No valid ACIS stations: "+str(errors))
    counts=Counter()
    for s in stations:counts.update(s["counts"])
    report=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source=SOURCE,input_sha256=hashlib.sha256(OBS.read_bytes()).hexdigest(),
        expected_observations=sum(map(len,expected.values())),station_count=len(expected),
        stations_retrieved=len(stations),counts=dict(counts),stations=stations,errors=errors,
        verified_intervals=0,training_labels_changed=False,
        note="Explicit station identity and same-date snowfall agreement within rounding tolerance. Shared upstream sources possible. Clocks are candidates only; timezone, effective date, accumulation interval and historical location unverified.")
    with gzip.open(OUT/"candidates.jsonl.gz","wt") as stream:
        for row in rows:stream.write(json.dumps(row,allow_nan=False)+"\n")
    (OUT/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="stations"}),flush=True)

if __name__=="__main__":main()
