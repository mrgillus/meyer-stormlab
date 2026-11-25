"""Match GHCN per-element observation clocks to the exact archived snowfall records.
No inferred UTC conversion or timing changes to existing training."""
import csv
import datetime as dt
import gzip
import json
import re
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import regional_archive as src
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/observation_clocks"
BASE="https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_station/"
def norm(s):
    s=s.strip()
    if not re.fullmatch(r"\d{4}",s):return None
    h,m=int(s[:2]),int(s[2:])
    return s if (0<=h<24 and 0<=m<60) or s=="2400" else None
def get(station,expected):
    url=BASE+station+".csv.gz"
    body=src.fetch(url).content
    rows=defaultdict(list)
    for r in csv.reader(gzip.decompress(body).decode("utf-8").splitlines()):
        if len(r)<8 or r[0]!=station or r[2]!="SNOW":continue
        if not re.fullmatch(r"\d{8}",r[1]):continue
        day=f"{r[1][:4]}-{r[1][4:6]}-{r[1][6:]}"
        if day not in expected:continue
        try:val=int(r[3])
        except ValueError:continue
        rows[day].append((val,r[4].strip(),r[5].strip(),r[6].strip(),norm(r[7])))
    counts=Counter();clocks=Counter();matched=[]
    for day,(val,mflag,sflag) in expected.items():
        matches=rows.get(day,[])
        if len(matches)!=1:counts["missing_or_duplicate"]+=1;continue
        v,m,q,s,t=matches[0]
        if v!=val or m!=mflag or s!=sflag or q:counts["value_or_flag_mismatch"]+=1;continue
        counts["exact_match"]+=1
        if t:counts["clock_present"]+=1;clocks[t]+=1
        else:counts["clock_missing"]+=1
        matched.append((station,day,val,m,s,t or ""))
    return dict(station=station,url=url,expected=len(expected),counts=dict(counts),clock_histogram=dict(clocks)),matched
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    expected=defaultdict(dict)
    with gzip.open(ROOT/"data/regional/observations.csv.gz","rt",newline="") as f:
        for r in csv.DictReader(f):
            expected[r["station"]][r["reported_date"]]=(int(r["snow_mm"]),r["measurement_flag"].strip(),r["source_flag"].strip())
    summaries=[];rows=[];errors=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for station,f in [(s,ex.submit(get,s,v)) for s,v in expected.items()]:
            try:
                report,rr=f.result();summaries.append(report);rows.extend(rr)
                print(station,report["counts"],flush=True)
            except Exception as e:errors.append(dict(station=station,error=str(e)))
    total=Counter()
    for s in summaries:total.update(s["counts"])
    with gzip.open(OUT/"matched_clocks.csv.gz","wt",newline="") as f:
        w=csv.writer(f);w.writerow(["station","reported_date","snow_mm","measurement_flag","source_flag","observation_clock"]);w.writerows(sorted(rows))
    report=dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),expected_observations=sum(len(v) for v in expected.values()),station_count=len(expected),stations=summaries,counts=dict(total),errors=errors,
        source_documentation="https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme-by_station.txt",
        note="Exact station/date/element/value/measurement flag/source flag match with clear quality flag. OBS-TIME originates in HOMR. Local clock alone does not establish time zone convention, effective 24h interval, or whether date is interval start/end. No training labels changed; missing clocks not imputed.")
    (OUT/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="stations"}),flush=True)
if __name__=="__main__":main()
