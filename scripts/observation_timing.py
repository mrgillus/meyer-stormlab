"""Audit NOAA snowfall observation timestamps without guessing missing clocks."""
import collections
import csv
import datetime as dt
import gzip
import io
import json
import pathlib
from concurrent.futures import ThreadPoolExecutor
import regional_archive as source
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"gefs_archive"
def audit(station,expected):
    ident=station["id"]
    r=source.fetch(source.NOAA+"by_station/"+ident+".csv.gz")
    data=gzip.decompress(r.content).decode()
    counts=collections.Counter(); matches=0; mismatches=0; seen=set(); records=[]
    for row in csv.reader(io.StringIO(data)):
        if len(row)<8 or row[0]!=ident or row[2]!="SNOW":continue
        date=row[1]
        if len(date)!=8:continue
        day=date[:4]+"-"+date[4:6]+"-"+date[6:]
        if day not in expected:continue
        try:value=int(row[3])
        except ValueError:continue
        if row[5].strip() or row[4].strip() in ("P","A") or value<0:continue
        if day in seen:continue
        seen.add(day)
        if value!=expected[day]:mismatches+=1;continue
        matches+=1
        clock=row[7].strip()
        if not (len(clock)==4 and clock.isdigit() and (clock=="2400" or (int(clock[:2])<24 and int(clock[2:])<60))):clock="unknown"
        counts[clock]+=1
        records.append([ident,day,value,clock,row[6]])
    return dict(id=ident,name=station["name"],expected_days=len(expected),matched=matches,
                value_mismatches=mismatches,missing_rows=len(expected)-len(seen),
                clock_counts=dict(counts),clock_known=matches-counts["unknown"]),records
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    stations=json.loads((ROOT/"data/regional/stations.json").read_text())
    values=collections.defaultdict(dict)
    with gzip.open(ROOT/"data/regional/observations.csv.gz","rt") as f:
        for r in csv.DictReader(f):values[r["station"]][r["reported_date"]]=int(r["snow_mm"])
    results=[];errors=[];records=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for s,f in [(s,ex.submit(audit,s,values[s["id"]])) for s in stations if s["usable_days"]>0]:
            try:a,b=f.result();results.append(a);records.extend(b)
            except Exception as e:errors.append(dict(id=s["id"],error=str(e)))
    report=dict(generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),stations=results,errors=errors,
                matched=sum(s["matched"] for s in results),clock_known=sum(s["clock_known"] for s in results),
                value_mismatches=sum(s["value_mismatches"] for s in results),
                note="Reported clocks are preserved verbatim. Local time convention, end-of-day labeling and source-specific reporting windows must be established before UTC joins; no inferred timestamps or training labels are issued.")
    (OUT/"observation_timing.json").write_text(json.dumps(report,indent=2)+"\n")
    with gzip.open(OUT/"observation_clocks.csv.gz","wt",newline="") as f:
        w=csv.writer(f);w.writerow(["station","reported_date","snow_mm","reported_clock","source_flag"]);w.writerows(records)
    print(json.dumps({k:v for k,v in report.items() if k!="stations"}),flush=True)
if __name__=="__main__":main()
