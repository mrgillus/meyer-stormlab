"""Import NOAA official 1991–2020 daily station temperature normals (°F)."""
import csv
import datetime as dt
import io
import json
import math
import pathlib
import urllib.request

DEST = pathlib.Path(__file__).resolve().parents[1] / "data/events/climatology.json"
BASE = "https://www.ncei.noaa.gov/data/normals-daily/1991-2020/access/"
STATIONS = ("USW00013994", "USC00237397", "USW00013802", "USC00238725",
            "USC00232591", "USC00114489", "USC00232220")
PLACES = {"Wentzville": (38.8114,-90.8529), "St. Louis": (38.6270,-90.1994),
          "St. Charles": (38.7881,-90.4974), "Affton": (38.5581,-90.3366),
          "Wildwood": (38.5920,-90.5587), "Washington": (38.5584,-91.0121),
          "Belleville": (38.5201,-89.9839), "Alton": (38.8906,-90.1843)}
FIELDS = {"high": "DLY-TMAX-NORMAL", "low": "DLY-TMIN-NORMAL",
          "high_sd": "DLY-TMAX-STDDEV", "low_sd": "DLY-TMIN-STDDEV"}

def parse_csv(raw, station):
    rows = csv.DictReader(io.StringIO(raw))
    if not set(FIELDS.values()).union({"STATION","DATE","LATITUDE","LONGITUDE"}).issubset(rows.fieldnames or []):
        raise ValueError("Missing NOAA daily normals columns")
    days, meta = {}, None
    for row in rows:
        if row["STATION"] != station:
            raise ValueError("Unexpected station ID")
        try:
            month, day = map(int, row["DATE"].split("-")[-2:])
            key = dt.date(2000, month, day).strftime("%m-%d")
            v = {k: float(row[field]) for k, field in FIELDS.items()}
            if not all(math.isfinite(x) for x in v.values()):
                continue
            if not (-90 <= v["low"] <= v["high"] <= 150 and
                    0 < v["high_sd"] <= 60 and 0 < v["low_sd"] <= 60):
                continue
            lat, lon = float(row["LATITUDE"]), float(row["LONGITUDE"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            meta = {"station": station, "name": row.get("NAME", station),
                    "latitude": lat, "longitude": lon, "source_url": BASE+station+".csv"}
            days[key] = v
        except (ValueError, TypeError, KeyError):
            continue
    if not meta or len(days) < 365:
        raise ValueError("Incomplete temperature normals")
    return {**meta, "days": days}

def miles(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a,*b))
    h = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 3958.8*2*math.asin(min(1,math.sqrt(h)))

def main():
    if DEST.exists():
        try:
            cached = json.loads(DEST.read_text())
            if cached.get("period") == "1991-2020" and len(cached.get("places", {})) == len(PLACES):
                print("Using existing NOAA climate normals cache")
                return
        except (OSError, ValueError):
            pass
    stations, errors = {}, []
    for station in STATIONS:
        try:
            with urllib.request.urlopen(BASE+station+".csv", timeout=15) as response:
                data = parse_csv(response.read().decode("utf-8-sig"), station)
            stations[station] = data
        except Exception as exc:
            errors.append({"station": station, "error": str(exc)})
    places = {}
    for place, coords in PLACES.items():
        if not stations:
            break
        closest = min(stations.values(), key=lambda s: miles(coords,(s["latitude"],s["longitude"])))
        dist = miles(coords,(closest["latitude"],closest["longitude"]))
        if dist <= 50:
            places[place] = {"station": closest["station"], "distance_miles": round(dist,1)}
    if not places:
        print("NOAA normals unavailable; anomaly detection remains unknown.", errors)
        return
    if DEST.exists():
        try:
            old = json.loads(DEST.read_text())
            if len(old.get("places",{})) > len(places):
                print("Retaining more complete prior climatology")
                return
        except (OSError, ValueError):
            pass
    payload = {"schema": 1, "period": "1991-2020", "unit": "F",
               "source": "NOAA NCEI daily climate normals", "imported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "stations": stations, "places": places, "errors": errors}
    DEST.parent.mkdir(parents=True,exist_ok=True)
    tmp=DEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,separators=(",",":"),allow_nan=False)+"\n")
    tmp.replace(DEST)
    print(f"NOAA normals: {len(stations)} stations; {len(places)}/{len(PLACES)} forecast places")

if __name__=="__main__":
    main()
