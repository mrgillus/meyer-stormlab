"""Retrieve historical station metadata, retaining only explicit timing fields.
No personal observer details or raw metadata are published.
"""
import datetime as dt
import json
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
import regional_archive as source

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"gefs_archive"
BASE="https://www.ncei.noaa.gov/access/homr/services/station/search?"
TIMEKEY=re.compile(r"(observation.?time|obs.?time|time.?of.?observation|reporting.?time)",re.I)
SAFEKEY=re.compile(r"^(beginDate|endDate|startDate|effectiveDate|date|time|value|code|element|elementCode|observationTime|obsTime|reportingTime)$",re.I)
SAFEVALUE=re.compile(r"^(?:\d{1,4}(?::\d{2})?(?: ?[AP]M)?|\d{4}-\d{2}-\d{2}(?:T[0-9:.Z+-]+)?|SNOW|SNWD|PRCP|TMAX|TMIN|LST|LDT|UTC|CST|CDT)$",re.I)
def compact(v):
    if isinstance(v,dict):
        return {k:w for k,x in v.items() if SAFEKEY.fullmatch(k) and (w:=compact(x)) is not None}
    if isinstance(v,list):return [w for x in v if (w:=compact(x)) is not None]
    if isinstance(v,(str,int)) and SAFEVALUE.fullmatch(str(v).strip()):return v
    return None
def find_timing(v,path="station"):
    result=[]
    if isinstance(v,dict):
        for k,x in v.items():
            if TIMEKEY.search(k):
                safe=compact(x)
                if safe:result.append({"field":k,"value":safe})
            else:result.extend(find_timing(x,path))
    elif isinstance(v,list):
        for x in v:result.extend(find_timing(x,path))
    return result
def get(s):
    url=BASE+urlencode(dict(qid="GHCND:"+s["id"],begindate="2000-01-01",enddate="2019-12-31",definitions="false",phrData="true"))
    raw=source.fetch(url).json()
    st=raw.get("stationCollection",{}).get("stations",[])
    return {"station":s["id"],"name":s["name"],"matches":len(st),"timing_candidates":find_timing(st)}
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    stations=json.loads((ROOT/"data/regional/stations.json").read_text()); rows=[];errors=[]
    with ThreadPoolExecutor(max_workers=3) as ex:
        for s,f in [(s,ex.submit(get,s)) for s in stations if s["usable_days"]>0]:
            try:rows.append(f.result())
            except Exception as e:errors.append({"station":s["id"],"error":str(e)})
    result=dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),stations=rows,errors=errors,
        matched_stations=sum(x["matches"]==1 for x in rows),
        timing_candidates=sum(bool(x["timing_candidates"]) for x in rows),
        note="HOMR metadata candidates only; snowfall element, effective dates, and time zone need independent verification. No inferred reporting windows are used for training.")
    (OUT/"homr_timing.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="stations"}))
if __name__=="__main__":main()
