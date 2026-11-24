"""Capture live stitched forecast guidance; timestamps are retrieval, not initialization."""
import datetime as dt
import json
import pathlib
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MODELS = {"GFS": ("ncep_gfs_global", 15), "ECMWF": ("ecmwf_ifs025", 10),
          "GEM": ("cmc_gem_gdps", 10), "ICON": ("icon_global", 7)}
PLACES = {"Wentzville": (38.8114,-90.8529), "St. Louis": (38.6270,-90.1994),
          "St. Charles": (38.7881,-90.4974), "Affton": (38.5581,-90.3366),
          "Wildwood": (38.5920,-90.5587), "Washington": (38.5584,-91.0121),
          "Belleville": (38.5201,-89.9839), "Alton": (38.8906,-90.1843)}
FIELDS = ("snowfall_sum","precipitation_sum","temperature_2m_min",
          "temperature_2m_max","wind_gusts_10m_max")
def request(model, days, lat, lon):
    params = dict(latitude=lat, longitude=lon, models=model, daily=",".join(FIELDS),
                  timezone="America/Chicago", temperature_unit="fahrenheit",
                  wind_speed_unit="mph", precipitation_unit="inch", forecast_days=days)
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                obj = json.load(response)
            daily = obj["daily"]
            if not daily["time"] or not all(len(daily[k]) == len(daily["time"]) for k in FIELDS):
                raise ValueError("Malformed daily series")
            return {"daily": daily, "units": obj["daily_units"],
                    "grid_latitude": obj["latitude"], "grid_longitude": obj["longitude"],
                    "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        except (OSError, ValueError, KeyError) as exc:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False)+"\n")
    tmp.replace(path)
def main():
    now = dt.datetime.now(dt.timezone.utc)
    snap = {"schema": 1, "retrieved_at": now.isoformat(),
            "source": "Open-Meteo Forecast API", "kind": "stitched_guidance_snapshot",
            "locations": {}, "errors": []}
    for place, (lat, lon) in PLACES.items():
        snap["locations"][place] = {}
        for label, (model, days) in MODELS.items():
            try:
                snap["locations"][place][label] = request(model,days,lat,lon)
            except Exception as exc:
                snap["errors"].append({"place":place,"model":label,"error":str(exc)})
    count = sum(len(v) for v in snap["locations"].values())
    print(f"Retrieved {count}/{len(PLACES)*len(MODELS)} location-model combinations")
    if not count:
        raise SystemExit("No valid guidance; archive unchanged")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    write(DATA / "snapshots" / now.strftime("%Y-%m") / (stamp+".json"),snap)
    history_path = DATA / "history.json"
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    history = [s for s in history if s["retrieved_at"] != snap["retrieved_at"]]
    history.append(snap)
    write(history_path, sorted(history,key=lambda s:s["retrieved_at"])[-28:])
    if snap["errors"]:
        print(json.dumps(snap["errors"]))
if __name__ == "__main__":
    main()
