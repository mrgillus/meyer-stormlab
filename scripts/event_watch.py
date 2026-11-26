"""Build a persistent, source-traceable regional pattern-watch catalog.

Uses existing, stitched Open-Meteo forecast captures. Never claims a model
initialization time, a calibrated probability, or a ground observation.
No network calls and no third-party dependencies.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import statistics
from collections import defaultdict
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEST = DATA / "events"
UTC = dt.timezone.utc
CT = ZoneInfo("America/Chicago")
SCHEMA = 1
MODELS = ("GFS", "ECMWF", "GEM", "ICON")
# Screening criteria, not official warning thresholds or event probabilities.
THRESHOLDS = {
    "snow": ("snowfall_sum", 2.0, "gte", "in"),
    "precipitation": ("precipitation_sum", 1.25, "gte", "in"),
    "wind": ("wind_gusts_10m_max", 45.0, "gte", "mph"),
    "cold": ("temperature_2m_min", 10.0, "lte", "F"),
    "heat": ("temperature_2m_max", 100.0, "gte", "F"),
}
LABELS = {
    "snow": "Snowfall potential",
    "precipitation": "Heavy precipitation signal",
    "wind": "Strong wind signal",
    "cold": "Unusual cold signal",
    "heat": "Extreme heat signal",
}
UNIT_ALIASES = {
    "in": {"inch": 1., "in": 1., "cm": 1/2.54, "mm": 1/25.4},
    "mph": {"mp/h": 1., "mph": 1., "km/h": 1/1.609344, "m/s": 2.2369362921},
    "F": {"°F": 1., "°C": None},
}


def read_json(path, default):
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def normalized(value, unit, target):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if target == "F" and unit == "°C":
        return value * 9/5 + 32
    multiplier = UNIT_ALIASES[target].get(unit)
    if multiplier is None:
        return None
    return value * multiplier


def captured_signals(snapshot):
    """For each kind, cluster consecutive calendar days across the metro."""
    recorded = parse_time(snapshot["retrieved_at"])
    today = recorded.astimezone(CT).date()
    detected = defaultdict(list)
    for place, by_model in (snapshot.get("locations") or {}).items():
        if not isinstance(by_model, dict):
            continue
        for model, sample in by_model.items():
            if model not in MODELS or not isinstance(sample, dict):
                continue
            daily, units = sample.get("daily") or {}, sample.get("units") or {}
            for idx, date in enumerate(daily.get("time") or []):
                try:
                    day = dt.date.fromisoformat(date)
                except (TypeError, ValueError):
                    continue
                # Daily forecasts may contain partially elapsed hours on day 0.
                if (day - today).days < 0 or (day - today).days > 16:
                    continue
                for kind, (field, threshold, operation, target) in THRESHOLDS.items():
                    series = daily.get(field) or []
                    raw = series[idx] if idx < len(series) else None
                    value = normalized(raw, units.get(field), target)
                    if value is None:
                        continue
                    hit = value >= threshold if operation == "gte" else value <= threshold
                    if hit:
                        detected[kind].append({
                            "date": day.isoformat(), "place": place,
                            "model": model, "value": round(value, 4),
                        })
    grouped = []
    for kind, hits in detected.items():
        hits.sort(key=lambda x: (x["date"], x["place"], x["model"]))
        segments = []
        for h in hits:
            day = dt.date.fromisoformat(h["date"])
            if not segments or (day - dt.date.fromisoformat(segments[-1][-1]["date"])).days > 1:
                segments.append([])
            segments[-1].append(h)
        for segment in segments:
            dates = sorted({x["date"] for x in segment})
            support = sorted({x["model"] for x in segment})
            places = sorted({x["place"] for x in segment})
            pairs = defaultdict(set)
            for x in segment:
                pairs[(x["date"], x["place"])].add(x["model"])
            corroboration = max((len(p) for p in pairs.values()), default=0)
            samples = sorted(segment, key=lambda x: x["value"], reverse=kind!="cold")
            strongest = samples[0]
            local = [x for x in segment if x["place"] == "Wentzville"]
            # These are threshold-triggering daily model values, not a storm-total blend.
            local_models = sorted({x["model"] for x in local})
            local_values = [x["value"] for x in local]
            grouped.append({
                "kind": kind, "title": LABELS[kind],
                "start_date": dates[0], "end_date": dates[-1],
                "models": support, "locations": places,
                "same_place_day_support": corroboration,
                "peak": strongest,
                "threshold": {"value": THRESHOLDS[kind][1], "unit": THRESHOLDS[kind][3],
                              "test": THRESHOLDS[kind][2]},
                "wentzville": {
                    "models": local_models, "triggering_samples": len(local_values),
                    "min_triggering_value": min(local_values) if local_values else None,
                    "max_triggering_value": max(local_values) if local_values else None,
                    "median_triggering_value": round(statistics.median(local_values), 3)
                       if local_values else None,
                    "note": "Only threshold-triggering daily forecasts, not a storm total.",
                },
            })
    return sorted(grouped, key=lambda x: (x["start_date"], x["kind"]))


def overlaps(event, item):
    if event["kind"] != item["kind"]:
        return False
    left = dt.date.fromisoformat(event["start_date"])
    right = dt.date.fromisoformat(event["end_date"])
    candidate_left = dt.date.fromisoformat(item["start_date"])
    candidate_right = dt.date.fromisoformat(item["end_date"])
    # Allow two days of forecast-date movement across successive captures.
    return candidate_left <= right + dt.timedelta(days=2) and candidate_right >= left - dt.timedelta(days=2)


def event_id(item, used):
    token = item["kind"] + "-" + item["start_date"].replace("-", "")
    if token not in used:
        return token
    index = 2
    while token + "-" + str(index) in used:
        index += 1
    return token + "-" + str(index)


def upsert(events, item, stamp, used):
    matches = [v for v in events if v["id"] not in used and overlaps(v, item)]
    if matches:
        # Prefer the most recently seen case if multiple similar older events exist.
        case = max(matches, key=lambda x: x["last_seen"])
        case["start_date"] = item["start_date"]
        case["end_date"] = item["end_date"]
    else:
        case = {
            "id": event_id(item, {v["id"] for v in events}),
            "kind": item["kind"], "title": item["title"],
            "first_seen": stamp, "last_seen": stamp,
            "start_date": item["start_date"], "end_date": item["end_date"],
            "timeline": [], "observation_status": "not_connected",
            "verification_status": "not_verified",
        }
        events.append(case)
    used.add(case["id"])
    case["last_seen"] = stamp
    case["latest"] = item
    case["timeline"].append({
        "captured_at": stamp, "start_date": item["start_date"],
        "end_date": item["end_date"], "models": item["models"],
        "locations": item["locations"],
        "same_place_day_support": item["same_place_day_support"],
        "peak": item["peak"], "wentzville": item["wentzville"],
    })
    return case


def main():
    history = read_json(DATA / "history.json", [])
    if not isinstance(history, list):
        raise ValueError("data/history.json must contain a list")
    captures = sorted((s for s in history if isinstance(s, dict) and s.get("retrieved_at")),
                      key=lambda x: x["retrieved_at"])
    prior = read_json(DEST / "catalog.json", {})
    if prior and prior.get("schema") != SCHEMA:
        raise ValueError("Unsupported existing event catalog schema")
    events = prior.get("events", [])
    processed = set(prior.get("processed_captures", []))
    if not isinstance(events, list):
        raise ValueError("Corrupt event catalog")
    new_count = 0
    for capture in captures:
        stamp = capture["retrieved_at"]
        if stamp in processed:
            continue
        per_capture_ids = set()
        for item in captured_signals(capture):
            upsert(events, item, stamp, per_capture_ids)
        processed.add(stamp)
        new_count += 1

    latest_stamp = captures[-1]["retrieved_at"] if captures else None
    now = dt.datetime.now(UTC)
    today = now.astimezone(CT).date()
    summarized = []
    for event in events:
        if not event.get("timeline"):
            continue
        seen_latest = event["last_seen"] == latest_stamp
        ended = dt.date.fromisoformat(event["end_date"]) < today
        status = ("forecast_window_passed" if ended else
                  "in_latest_capture" if seen_latest else "not_in_latest_capture")
        item = event["latest"]
        start = dt.date.fromisoformat(event["start_date"])
        summary = {
            "id": event["id"], "kind": event["kind"], "title": event["title"],
            "start_date": event["start_date"], "end_date": event["end_date"],
            "first_seen": event["first_seen"], "last_seen": event["last_seen"],
            "snapshots_seen": len(event["timeline"]), "status": status,
            "lead_days": (start - today).days,
            "models": item["models"], "locations": item["locations"],
            "same_place_day_support": item["same_place_day_support"],
            "peak": item["peak"], "threshold": item["threshold"],
            "wentzville": item["wentzville"],
            "observation_status": event["observation_status"],
            "verification_status": event["verification_status"],
            "source": "Open-Meteo stitched daily guidance, not model-cycle provenance",
        }
        summarized.append(summary)
    summarized.sort(key=lambda x: (x["status"] != "in_latest_capture",
                                   x["status"] == "forecast_window_passed",
                                   max(0, x["lead_days"]), -x["same_place_day_support"],
                                   -x["snapshots_seen"]))
    catalog = {
        "schema": SCHEMA, "updated_at": now.isoformat(),
        "processed_captures": sorted(processed), "events": events,
    }
    latest_age = ((now - parse_time(latest_stamp)).total_seconds()/3600
                  if latest_stamp else None)
    index = {
        "schema": SCHEMA, "generated_at": now.isoformat(),
        "source_latest_capture": latest_stamp,
        "capture_age_hours": round(latest_age, 2) if latest_age is not None else None,
        "capture_count_processed": len(processed),
        "status": "current" if latest_age is not None and latest_age < 12 else "stale",
        "screening_rules": {k: {"field": v[0], "threshold": v[1], "unit": v[3],
                                "direction": v[2]} for k, v in THRESHOLDS.items()},
        "limitations": [
            "Research screening signals, not official NWS alerts or calibrated probabilities.",
            "Daily deterministic stitched forecasts; model initialization not identified.",
            "Signals cannot establish severe thunderstorms, tornadoes, flood risk, or snow depth.",
            "Forecast history is not ground truth; no model skill verdict without observations.",
            "15-day GFS is the longest available collector input. No 17–21 day detection.",
        ],
        "events": summarized[:40],
    }
    # Never destroy existing event history if no fresh capture is available.
    if captures or not (DEST / "index.json").exists():
        write_json(DEST / "catalog.json", catalog)
        write_json(DEST / "index.json", index)
    print("StormLab pattern watch: processed %s new captures; %s events; %s active signals"
          % (new_count, len(events), sum(x["status"] == "in_latest_capture" for x in summarized)))


if __name__ == "__main__":
    main()
