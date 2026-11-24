"""Regional observation inventory and bounded GEFS reforecast extraction.
Separate experimental dataset; no promotion to live forecasts.
"""
import csv
import datetime as dt
import gzip
import json
import math
import pathlib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import requests

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"regional"
NOAA="https://www.ncei.noaa.gov/pub/data/ghcn/daily/"
S3="https://noaa-gefs-retrospective.s3.amazonaws.com/GEFSv12/reforecast/"
STATION="USW00013994"
CENTER=(38.75,-90.37)
YEARS=range(2000,2020)
MONTHS=(1,2,3,10,11,12)
LEADS=(24,72,168,336)
MEMBERS=("c00","p01")
PILOT=("20000105","20100205","20190108")
FIELDS=(("t2m","tmp_2m","TMP","2 m above ground"),
        ("t850","tmp_pres","TMP","850 mb"),
        ("z500","hgt_pres","HGT","500 mb"),
        ("mslp","pres_msl","PRMSL",None),
        ("pwat","pwat_eatm","PWAT",None))
# Pressure name can be PRMSL or PRES depending on archive encoding.
POINTS=[(lat,lon) for lat in (32,38,44) for lon in (-102,-92,-82)]
POINTS.append(CENTER)

def fetch(url,headers=None):
    for i in range(3):
        try:
            r=requests.get(url,headers=headers,timeout=(10,45))
            if r.status_code in (404,416): r.raise_for_status()
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if i==2 or getattr(e.response,"status_code",None) in (404,416): raise
            time.sleep(2**i)

def distance(lat,lon):
    a,b=map(math.radians,CENTER); c,d=map(math.radians,(lat,lon))
    return 3958.8*2*math.asin(math.sqrt(math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2))

def station_rows():
    candidates=[]
    for line in fetch(NOAA+"ghcnd-inventory.txt").text.splitlines():
        v=line.split()
        if len(v)!=6 or v[3]!="SNOW" or not v[0].startswith("US"): continue
        ident,lat,lon,_,first,last=v
        lat,lon=float(lat),float(lon)
        if int(first)>2004 or int(last)<2015 or distance(lat,lon)>110: continue
        candidates.append((ident,lat,lon,int(first),int(last)))
    names={}
    for line in fetch(NOAA+"ghcnd-stations.txt").text.splitlines():
        if len(line)>=71: names[line[:11]]=line[41:71].strip()
    # Interleave geographic cells to avoid a cluster of nearly collocated stations.
    cells={}
    for c in sorted(candidates,key=lambda c:(-(min(c[4],2019)-max(c[3],2000)),distance(c[1],c[2]))):
        cells.setdefault((math.floor(c[1]*2),math.floor(c[2]*2)),[]).append(c)
    selected=[c for c in candidates if c[0]==STATION]
    while len(selected)<30 and any(cells.values()):
        for key in sorted(cells,key=lambda k:distance((k[0]+.5)/2,(k[1]+.5)/2)):
            if cells[key] and len(selected)<30:
                c=cells[key].pop(0)
                if c not in selected: selected.append(c)
    def parse(c):
        ident,lat,lon,first,last=c
        rows=[]
        for line in fetch(NOAA+"all/"+ident+".dly").text.splitlines():
            if line[17:21]!="SNOW": continue
            y,m=int(line[11:15]),int(line[15:17])
            if y not in YEARS or m not in MONTHS: continue
            for i in range(31):
                s=line[21+8*i:29+8*i]
                if len(s)!=8: continue
                try: day=dt.date(y,m,i+1).isoformat(); value=int(s[:5])
                except ValueError: continue
                if value<0 or s[6]!=" " or s[5] in ("P","A"): continue
                rows.append((ident,day,value,s[5],s[7]))
        meta=dict(id=ident,name=names.get(ident,ident),latitude=lat,longitude=lon,
                  distance_miles=round(distance(lat,lon),1),inventory_first=first,inventory_last=last,
                  usable_days=len(rows),days_ge_1in=sum(v[2]>=25.4 for v in rows))
        return meta,rows
    stations=[]; obs=[]; errors=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures=[(c,ex.submit(parse,c)) for c in selected]
        for c,f in futures:
            try:
                meta,rows=f.result()
                stations.append(meta);obs.extend(rows)
            except Exception as e: errors.append({"station":c[0],"error":str(e)})
    if not obs: raise RuntimeError("No valid observations")
    with gzip.open(OUT/"observations.csv.gz","wt",newline="") as f:
        w=csv.writer(f);w.writerow(["station","reported_date","snow_mm","measurement_flag","source_flag"]);w.writerows(obs)
    with (OUT/"stations.json").open("w") as f: json.dump(stations,f,indent=2)
    return stations,obs,errors

def index(url):
    lines=fetch(url+".idx").text.splitlines()
    records=[]
    for line in lines:
        parts=line.split(":")
        if len(parts)>=6 and parts[1].isdigit():
            records.append((int(parts[1]),parts))
    if not records: raise ValueError("Empty GRIB index "+url)
    return [(off,records[i+1][0]-1 if i+1<len(records) else None,p)
            for i,(off,p) in enumerate(records)]

def decode(url,rec,points):
    from eccodes import codes_new_from_message,codes_grib_find_nearest,codes_release,codes_get
    start,end,parts=rec
    response=fetch(url,{"Range":f"bytes={start}-{'' if end is None else end}"})
    if response.status_code!=206: raise ValueError("Server did not honor GRIB byte range")
    if not response.headers.get("Content-Range","").startswith(f"bytes {start}-"):
        raise ValueError("Unexpected Content-Range")
    raw=response.content
    if not raw.startswith(b"GRIB") or not raw.endswith(b"7777"): raise ValueError("Invalid GRIB message framing")
    gid=codes_new_from_message(raw)
    try:
        data=[]
        for lat,lon in points:
            nearest=codes_grib_find_nearest(gid,lat,lon%360)[0]
            v=float(nearest["value"])
            if not math.isfinite(v) or abs(v)>1e15: raise ValueError("Missing grid value")
            data.append(v)
        return {"values":data,"units":codes_get(gid,"units"),"short_name":codes_get(gid,"shortName"),
                "data_date":int(codes_get(gid,"dataDate")),"data_time":int(codes_get(gid,"dataTime")),
                "end_step":int(codes_get(gid,"endStep"))}
    finally: codes_release(gid)

def file_url(date,member,lead,field):
    stamp=date+"00"; folder="Days:1-10" if lead<=240 else "Days:10-16"
    if field=="hgt_pres" and lead<=240: field+="_abv700mb"
    return f"{S3}{date[:4]}/{stamp}/{member}/{folder}/{field}_{stamp}_{member}.grib2"

def extract(date,member):
    rows=[];errors=[]
    for name,field,var,level in FIELDS:
        for group in ((24,72,168),(336,)):
            url=file_url(date,member,group[0],field)
            try:
                records=index(url)
                for lead in group:
                    match=[r for r in records if r[2][3] in ({var,"PRES","MSLET"} if name=="mslp" else {var})
                           and (level is None or r[2][4]==level)
                           and r[2][5].strip()==f"{lead} hour fcst"]
                    if len(match)!=1:
                        errors.append(dict(date=date,member=member,field=name,lead=lead,error=f"Expected 1 matching message, got {len(match)}"));continue
                    try:
                        data=decode(url,match[0],POINTS)
                        if data["data_date"]!=int(date) or data["data_time"]!=0 or data["end_step"]!=lead:
                            raise ValueError("GRIB metadata disagrees with requested initialization/lead")
                        rows.append(dict(init=date+"T00Z",member=member,lead_hours=lead,field=name,**data))
                    except Exception as e: errors.append(dict(date=date,member=member,field=name,lead=lead,error=str(e)))
            except Exception as e: errors.append(dict(date=date,member=member,field=name,leads=group,error=str(e)))
    # Use only non-overlapping six-hour accumulation messages for a 24h total.
    for lead in LEADS:
        url=file_url(date,member,lead,"apcp_sfc")
        try:
            records=index(url); blocks=[]
            for end in range(lead-18,lead+1,6):
                matches=[r for r in records if r[2][3]=="APCP" and r[2][5].strip()==f"{end-6}-{end} hour acc fcst"]
                if len(matches)!=1: raise ValueError(f"Missing/ambiguous {end-6}-{end}h accumulation")
                d=decode(url,matches[0],POINTS)
                if d["data_date"]!=int(date) or d["data_time"]!=0 or d["end_step"]!=end:
                    raise ValueError("Precipitation metadata mismatch")
                if d["units"] not in ("kg m**-2","kg m-2","mm"): raise ValueError("Unexpected precipitation units "+d["units"])
                blocks.append(d["values"])
            rows.append(dict(init=date+"T00Z",member=member,lead_hours=lead,field="precip24",
                values=[sum(x) for x in zip(*blocks)],units="mm water equivalent"))
        except Exception as e: errors.append(dict(date=date,member=member,field="precip24",lead=lead,error=str(e)))
    return rows,errors

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    stations,obs,station_errors=station_rows()
    print("Stations",len(stations),"observation rows",len(obs),flush=True)
    rows=[];errors=[]
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs=[ex.submit(extract,date,member) for date in PILOT for member in MEMBERS]
        for f in fs:
            rr,ee=f.result();rows.extend(rr);errors.extend(ee);print("GRIB messages/derived rows",len(rows),"errors",len(errors),flush=True)
    result=dict(schema=1,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        archive_period=[2000,2019],station_count=len(stations),observation_rows=len(obs),
        significant_station_days=sum(r[2]>=25.4 for r in obs),
        unique_significant_dates=len({r[1] for r in obs if r[2]>=25.4}),
        station_errors=station_errors,gefs_errors=errors,pilot_initializations=PILOT,
        members=MEMBERS,lead_hours=LEADS,fields=[f[0] for f in FIELDS]+["precip24"],
        expected_feature_rows=len(PILOT)*len(MEMBERS)*len(LEADS)*(len(FIELDS)+1),
        extracted_feature_rows=len(rows),points=POINTS,
        limitations=["Pilot only: no regional forecast model trained.","Observation reporting times are not harmonized with UTC forecast windows; labels are not joined yet.","Station-days are spatially correlated, not independent storms.","Precipitation is liquid equivalent, not snowfall."])
    (OUT/"report.json").write_text(json.dumps(result,indent=2)+"\n")
    with gzip.open(OUT/"gefs_pilot.json.gz","wt") as f:json.dump(rows,f,separators=(",",":"))
    print(json.dumps(result,indent=2),flush=True)
if __name__=="__main__":main()
