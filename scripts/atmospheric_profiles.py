"""Atmospheric profile pilot. Exact GRIB provenance, independent of existing archives.
No precipitation-type, SLR, or ground-accumulation forecast is promoted."""
import datetime as dt
import gzip
import json
import math
import sys
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections import Counter
import numpy as np
from eccodes import codes_new_from_message,codes_get,codes_grib_find_nearest,codes_release
import regional_archive as src
import regional_live_v2 as v2
import regional_live as live

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/atmospheric_profiles"
PRESSURES=(925,850,700,600,500)
# Historical specific humidity is the primary moisture variable, not RH.
# Live RH is also collected independently to measure coverage, not silently substituted.
SPECS=[]
for prefix,filename,var,levels,units in (
    ("t","tmp_pres","TMP",PRESSURES,("K",)),
    ("q","spfh_pres","SPFH",PRESSURES,("kg kg**-1","kg kg-1","kg/kg","1")),
    ("omega","vvel_pres","VVEL",(850,700,600,500),("Pa s**-1","Pa s-1","Pa/s")),
    ("u","ugrd_pres","UGRD",(850,700),("m s**-1","m s-1","m/s")),
    ("v","vgrd_pres","VGRD",(850,700),("m s**-1","m s-1","m/s")),
    ("z","hgt_pres","HGT",(850,500),("gpm","m"))):
    for p in levels:
        file=filename+("_abv700mb" if p<700 else "")
        SPECS.append((f"{prefix}{p}",file,var,f"{p} mb",units))
SPECS.extend([
    ("t2m","tmp_2m","TMP","2 m above ground",("K",)),
    ("skin","tmp_sfc","TMP","surface",("K",)),
    ("soil0_10cm","tsoil_bgrnd","TSOIL","0-0.1 m below ground",("K",)),
    ("surface_pressure","pres_sfc","PRES","surface",("Pa",))])
RH=[(f"rh{p}",None,"RH",f"{p} mb",("%",)) for p in PRESSURES]
HIST_DATES=("20000105","20100205","20190108")
HIST_MEMBERS=("c00","p01")
LIVE_HOURS=(18,24,30,36,42,48)

@lru_cache(maxsize=128)
def index(url):return src.index(url)

def select(mode,date,member,lead,spec):
    name,file,var,level,units=spec
    step=f"{lead} hour fcst"
    if mode=="historical":
        # Probe both historical pressure products, bypassing the shared HGT redirect.
        base=file.removesuffix("_abv700mb")
        names=[file]
        if level.endswith(" mb"):
            names=list(dict.fromkeys([file,base,base+"_abv700mb"]))
        stamp=date+"00";errors=[]
        for candidate in names:
            url=f"{src.S3}{date[:4]}/{stamp}/{member}/Days:1-10/{candidate}_{stamp}_{member}.grib2"
            try:rows=index(url)
            except Exception as e:errors.append(str(e));continue
            matches=[r for r in rows if r[2][3]==var and r[2][4]==level and r[2][5].strip()==step]
            if len(matches)>1:raise ValueError("Ambiguous historical record")
            if matches:return url,matches[0]
        raise ValueError("No exact historical record in "+",".join(names)+"; "+" | ".join(errors)[:300])
    # Prefer fine grid at lower levels; above 700 hPa historical archive uses .5 degree.
    order=("a","b","s") if level.endswith(" mb") and int(level.split()[0])<700 else ("s","a","b")
    for part in order:
        try:url,rows=live.records(date,member,lead,part)
        except Exception:continue
        matches=[r for r in rows if r[2][3]==var and r[2][4]==level and r[2][5].strip()==step]
        if len(matches)>1:raise ValueError("Ambiguous live record")
        if matches:return url,matches[0]
    raise ValueError("No matching live field")

def decode(url,rec,date,lead,spec,points):
    name,file,var,level,units=spec
    start,end,_=rec
    response=src.fetch(url,{"Range":f"bytes={start}-{'' if end is None else end}"})
    if response.status_code!=206 or not response.headers.get("Content-Range","").startswith(f"bytes {start}-"):raise ValueError("Invalid GRIB byte range")
    raw=response.content
    if not raw.startswith(b"GRIB") or not raw.endswith(b"7777"):raise ValueError("Invalid GRIB framing")
    g=codes_new_from_message(raw)
    try:
        md={k:codes_get(g,k) for k in ("dataDate","dataTime","startStep","endStep","stepType","units","shortName","typeOfLevel","level","iDirectionIncrementInDegrees","jDirectionIncrementInDegrees")}
        if md["dataDate"]!=int(date) or md["dataTime"]!=0 or md["endStep"]!=lead or md["stepType"]!="instant":raise ValueError("GRIB temporal provenance mismatch")
        if md["units"] not in units:raise ValueError("Unexpected units "+str(md["units"]))
        if level.endswith(" mb") and (md["typeOfLevel"]!="isobaricInhPa" or md["level"]!=int(level.split()[0])):raise ValueError("Pressure-level mismatch")
        values=[];grids=[]
        for p in points:
            n=codes_grib_find_nearest(g,p["lat"],p["lon"]%360)[0]
            val=float(n["value"])
            if var!="TSOIL" and (not math.isfinite(val) or abs(val)>1e15):raise ValueError("Invalid value")
            if var=="TSOIL" and not 170<val<350:
                values.append(None);grids.append([float(n["lat"]),float((n["lon"]+180)%360-180)]);continue
            if var=="TMP" and not 170<val<350:raise ValueError("Temperature range")
            if var=="SPFH" and not 0<=val<.1:raise ValueError("Specific humidity range")
            if var=="RH" and not 0<=val<=110:raise ValueError("Relative humidity range")
            if var=="PRES" and not 50000<val<110000:raise ValueError("Surface pressure range")
            values.append(val);grids.append([float(n["lat"]),float((n["lon"]+180)%360-180)])
        if not any(v is not None for v in values):raise ValueError("No usable soil grid points")
        return dict(field=name,source=url,metadata=md,values=values,grid_coordinates=grids)
    finally:codes_release(g)

def task(mode,date,member,lead,points):
    rows=[];errors=[]
    for spec in SPECS+(RH if mode=="live" else []):
        try:
            url,rec=select(mode,date,member,lead,spec)
            rows.append(decode(url,rec,date,lead,spec,points))
        except Exception as e:errors.append(dict(field=spec[0],error=str(e)))
    print(mode,date,member,lead,len(rows),"fields",len(errors),"missing",flush=True)
    return dict(date=date,member=member,lead_hours=lead,rows=rows,errors=errors)

def diagnostics(task,points):
    vals={r["field"]:r["values"] for r in task["rows"]}
    def at(f,i):return vals[f][i] if f in vals else None
    out=[]
    for i,p in enumerate(points):
        if p.get("kind")!="town":continue
        ps=at("surface_pressure",i)
        levels=[]
        for pressure in PRESSURES:
            t=at(f"t{pressure}",i);q=at(f"q{pressure}",i);omega=at(f"omega{pressure}",i);rh=at(f"rh{pressure}",i)
            above=ps is not None and pressure*100<ps
            # Relative humidity w.r.t. liquid water, diagnostic only.
            derived=None
            if above and t is not None and q is not None:
                tc=t-273.15;e=q*pressure*100/(.622+.378*q)
                es=611.2*math.exp(17.67*tc/(tc+243.5))
                derived=100*e/es
            levels.append(dict(pressure_hpa=pressure,above_surface=above,temp_c=round(t-273.15,2) if above and t is not None else None,
                rh_water_derived_pct=round(derived,1) if derived is not None else None,native_rh_pct=rh if above else None,omega_pa_s=omega if above else None))
        dgz=[l["pressure_hpa"] for l in levels if l["temp_c"] is not None and -18<=l["temp_c"]<=-12 and l["omega_pa_s"] is not None and l["omega_pa_s"]<0]
        t2=at("t2m",i);skin=at("skin",i);soil=at("soil0_10cm",i)
        z850,z500=at("z850",i),at("z500",i)
        out.append(dict(town=p["label"],temp2m_c=round(t2-273.15,2) if t2 is not None else None,skin_c=round(skin-273.15,2) if skin is not None else None,soil_c=round(soil-273.15,2) if soil is not None else None,
            thickness_850_500_m=round(z500-z850,1) if z850 is not None and z500 is not None and ps is not None and ps>85000 else None,
            warm_sampled_layers_hpa=[l["pressure_hpa"] for l in levels if l["temp_c"] is not None and l["temp_c"]>0],
            sampled_dgz_ascent_hpa=dgz,levels=levels))
    return out

def main():
    mode=sys.argv[1]
    if mode not in ("historical","live"):raise ValueError("Unknown mode")
    manifest=json.loads((live.ARCH/"manifest.json").read_text())
    points=[dict(p,kind="synoptic" if i<10 else "station") for i,p in enumerate(manifest["points"])]
    points.extend(dict(label=p["name"],lat=p["lat"],lon=p["lon"],kind="town") for p in v2.storm.POINTS if p["kind"]=="town")
    jobs=[(d,m,l) for d in HIST_DATES for m in HIST_MEMBERS for l in (24,48)] if mode=="historical" else [("20261125",m,l) for m in live.MEMBERS for l in LIVE_HOURS]
    results=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for f in [pool.submit(task,mode,*j,points) for j in jobs]:results.append(f.result())
    requested=[s[0] for s in SPECS+(RH if mode=="live" else [])]
    coverage=Counter(r["field"] for t in results for r in t["rows"])
    grids={f:sorted({r["metadata"]["iDirectionIncrementInDegrees"] for t in results for r in t["rows"] if r["field"]==f}) for f in requested}
    summary=dict(mode=mode,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),manifest_fingerprint=manifest["fingerprint"],
        point_count=len(points),expected_tasks=len(jobs),complete_tasks=sum(len(t["rows"])==len(requested) and all(all(v is not None for v in r["values"]) for r in t["rows"]) for t in results),
        requested_fields=requested,field_coverage={f:dict(available=coverage[f],expected=len(jobs),grid_degrees=grids[f]) for f in requested},
        errors=[dict(date=t["date"],member=t["member"],lead_hours=t["lead_hours"],**e) for t in results for e in t["errors"]],
        value_count=sum(sum(v is not None for v in r["values"]) for t in results for r in t["rows"]), missing_point_values=sum(sum(v is None for v in r["values"]) for t in results for r in t["rows"]),operational_ready=False,
        note="Availability and profile diagnostics only. Fixed pressure levels may miss warm noses and the dendritic growth zone; below-ground levels are masked in diagnostics. Specific humidity-derived RH uses water saturation; native RH is not substituted. Soil/skin temperatures are not road temperatures. No phase, SLR or accumulation forecast.")
    if mode=="live":
        summary["profiles"]=[dict(date=t["date"],member=t["member"],lead_hours=t["lead_hours"],towns=diagnostics(t,points)) for t in results]
    OUT.mkdir(parents=True,exist_ok=True)
    with gzip.open(OUT/f"{mode}_pilot.json.gz","wt") as f:json.dump(dict(points=points,summary=summary,records=results),f,separators=(",",":"),allow_nan=False)
    (OUT/f"{mode}_report.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n")
    print(json.dumps({k:v for k,v in summary.items() if k not in ("profiles","errors")}),flush=True)
if __name__=="__main__":main()
