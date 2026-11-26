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
PLACES = ("Wentzville", "St. Louis", "St. Charles", "Affton", "Wildwood",
          "Washington", "Belleville", "Alton")
WATCH_NAMES = ("Alder", "Birch", "Cedar", "Drift", "Ember", "Flint", "Gale",
               "Hawthorn", "Indigo", "Juniper", "Kestrel", "Linden", "Maple",
               "North", "Onyx", "Pine", "Quartz", "Raven", "Sable", "Thorn",
               "Umber", "Vale", "Willow", "Xenia", "Yarrow", "Zephyr")
# Screening criteria, not official warning thresholds or event probabilities.
THRESHOLDS = {
    "snow": ("snowfall_sum", 2.0, "gte", "in"),
    "light_snow": ("weather_code", 1, "gte", "signal"),
    "rain": ("rain_sum", 0.01, "gte", "in"),
    "showers": ("showers_sum", 0.01, "gte", "in"),
    "drizzle": ("weather_code", 1, "gte", "signal"),
    "ice": ("weather_code", 1, "gte", "signal"),
    "thunderstorm": ("weather_code", 1, "gte", "signal"),
    "fog": ("weather_code", 1, "gte", "signal"),
    "freeze": ("temperature_2m_min", 32, "lte", "F"),
    "heat_stress": ("apparent_temperature_max", 105, "gte", "F"),
    "cold_stress": ("apparent_temperature_min", 0, "lte", "F"),
    "precipitation": ("precipitation_sum", 1.25, "gte", "in"),
    "wind": ("wind_gusts_10m_max", 45.0, "gte", "mph"),
    "cold": ("temperature_2m_min", 10.0, "lte", "F"),
    "heat": ("temperature_2m_max", 100.0, "gte", "F"),
}
LABELS = {
    "snow": "Accumulating snowfall",
    "light_snow": "Light snow / flurries",
    "rain": "Rainfall",
    "showers": "Convective showers",
    "drizzle": "Drizzle",
    "ice": "Freezing rain / drizzle signal",
    "thunderstorm": "Thunderstorm signal",
    "fog": "Fog signal",
    "freeze": "Freezing temperature",
    "heat_stress": "Dangerous apparent heat signal",
    "cold_stress": "Dangerous apparent cold signal",
    "precipitation": "Heavy precipitation signal",
    "wind": "Strong wind signal",
    "cold": "Unusual cold signal",
    "heat": "Extreme heat signal",
}
# Daily weather code represents the day's most severe coded condition, not
# every phenomenon. A negative below means no matching code, not no hazard.
CODE_HITS = {
    "light_snow": {71, 77, 85},
    "drizzle": {51, 53, 55},
    "ice": {56, 57, 66, 67},
    "thunderstorm": {95, 96, 97, 99},
    "fog": {45, 48},
}
VALID_CODES = {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66,
               67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 97, 99}
ROUTINE_KINDS = {"rain", "showers", "drizzle", "light_snow", "fog", "freeze"}
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


def screen_value(kind, raw, unit):
    if kind in CODE_HITS:
        if (str(unit).lower() != "wmo code" or isinstance(raw, bool)
                or not isinstance(raw, (int, float)) or raw not in VALID_CODES):
            return None
        return float(raw in CODE_HITS[kind])
    return normalized(raw, unit, THRESHOLDS[kind][3])


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
                    value = screen_value(kind, raw, units.get(field))
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
            samples = sorted(segment, key=lambda x: x["value"], reverse=THRESHOLDS[kind][2] != "lte")
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
    today = parse_time(stamp).astimezone(CT).date().isoformat()
    matches = [v for v in events if v["id"] not in used and
               v["end_date"] >= today and overlaps(v, item)]
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


def assign_names(events):
    used = {e["name"] for e in events if e.get("name")}
    number = 0
    for event in sorted(events, key=lambda e: (e["first_seen"], e["id"])):
        if event.get("name"):
            continue
        while True:
            cycle, idx = divmod(number, len(WATCH_NAMES))
            number += 1
            name = WATCH_NAMES[idx] + (f" {cycle + 1}" if cycle else "")
            if name not in used:
                break
        event["name"] = name
        used.add(name)


def assess(snapshot, kind, start_date, end_date):
    """Confirmed support out of four sources, not a calibrated probability.
    A negative requires valid below-threshold values across all remaining
    dates and all eight locations. Missing or truncated data is unknown.
    """
    stamp = snapshot["retrieved_at"]
    today = parse_time(stamp).astimezone(CT).date()
    start = max(today, dt.date.fromisoformat(start_date))
    end = dt.date.fromisoformat(end_date)
    if start > end:
        return None
    dates = [(start + dt.timedelta(days=i)).isoformat()
             for i in range((end - start).days + 1)]
    field, threshold, operation, target = THRESHOLDS[kind]
    states = {}
    for model in MODELS:
        hit, complete = False, True
        for place in PLACES:
            sample = (snapshot.get("locations", {}).get(place, {}) or {}).get(model) or {}
            daily, units = sample.get("daily") or {}, sample.get("units") or {}
            by_date = dict(zip(daily.get("time") or [], daily.get(field) or []))
            for date in dates:
                value = screen_value(kind, by_date.get(date), units.get(field))
                if value is None:
                    complete = False
                elif (value >= threshold if operation == "gte" else value <= threshold):
                    hit = True
        states[model] = "supports" if hit else "clear" if complete else "unknown"
    support = sum(s == "supports" for s in states.values())
    unknown = sum(s == "unknown" for s in states.values())
    return {
        "captured_at": stamp, "start_date": start_date, "end_date": end_date,
        "models": states, "supporting_models": support, "unknown_models": unknown,
        "total_models": len(MODELS),
        "support_pct": round(100 * support / len(MODELS), 1) if support or not unknown else None,
        "possible_support_pct": round(100 * (support + unknown) / len(MODELS), 1),
    }


def update_assessments(events, captures):
    """Backfill only from retained real captures, then append idempotently."""
    for event in events:
        old = {x["captured_at"]: x for x in event.get("assessments", [])}
        positive = sorted(event.get("timeline", []), key=lambda t: parse_time(t["captured_at"]))
        for capture in captures:
            stamp = capture["retrieved_at"]
            if stamp in old:
                continue
            eligible = [x for x in positive if parse_time(x["captured_at"]) <= parse_time(stamp)]
            if not eligible:
                continue
            window = eligible[-1]
            point = assess(capture, event["kind"], window["start_date"], window["end_date"])
            if point is not None:
                old[stamp] = point
        event["assessments"] = sorted(old.values(), key=lambda t: parse_time(t["captured_at"]))


def group_events(summaries, previous=()):
    """Date-overlap parent events; hazard records remain independent.

    Keep the previous parent identity when its members still overlap in time.
    If a window splits, the component containing the original primary mode
    keeps the name. Overlap is a regional association, not proof of a single
    physical weather system.
    """
    components = []
    for item in sorted(summaries, key=lambda x: (x["start_date"], x["end_date"], x["id"])):
        if not components or item["start_date"] > components[-1]["end_date"]:
            components.append({"end_date": item["end_date"], "modes": []})
        comp = components[-1]
        comp["modes"].append(item)
        comp["end_date"] = max(comp["end_date"], item["end_date"])

    # Give original-primary components first claim on previous identities.
    primary_ids = {g["primary_mode_id"] for g in previous}
    components.sort(key=lambda c: (not any(m["id"] in primary_ids for m in c["modes"]),
                                   c["modes"][0]["start_date"]))
    used = set()
    reserved_names = {g["name"] for g in previous}
    reserved_ids = {g["id"] for g in previous}
    groups = []
    for comp in components:
        modes = comp["modes"]
        member_ids = {m["id"] for m in modes}
        matches = [g for g in previous if g["id"] not in used
                   and member_ids.intersection(g["mode_ids"])]
        # Do not steal a primary identity from another split component.
        matches = [g for g in matches if g["primary_mode_id"] in member_ids
                   or not any(g["primary_mode_id"] == m["id"] for c in components
                              for m in c["modes"])]
        primary = min(modes, key=lambda m: (m["first_seen"],
                      m["priority"] == "routine", m["kind"] != "snow", m["id"]))
        prior = min(matches, key=lambda g: (g["first_seen"], g["id"])) if matches else None
        if prior:
            gid, name = prior["id"], prior["name"]
            primary_id = prior["primary_mode_id"]
            if primary_id not in member_ids:
                primary_id = primary["id"]
        else:
            # A split or new event uses an unused child name and a fresh ID.
            candidates = sorted(modes, key=lambda m: (m != primary, m["first_seen"], m["id"]))
            chosen = next((m for m in candidates if m["name"] not in reserved_names), None)
            name = chosen["name"] if chosen else primary["name"] + " (split)"
            n = 2
            while name in reserved_names:
                name = primary["name"] + " (split " + str(n) + ")"
                n += 1
            gid = "event-" + primary["id"]
            n = 2
            while gid in reserved_ids:
                gid = "event-" + primary["id"] + "-group-" + str(n)
                n += 1
            primary_id = primary["id"]
        used.add(gid)
        used.update(g["id"] for g in matches)
        reserved_names.add(name)
        reserved_ids.add(gid)
        statuses = {m["status"] for m in modes}
        status = ("in_latest_capture" if "in_latest_capture" in statuses else
                  "not_in_latest_capture" if "not_in_latest_capture" in statuses else
                  "forecast_window_passed")
        aliases = {m["name"] for m in modes}
        for g in matches:
            aliases.add(g["name"])
            aliases.update(g.get("aliases", []))
        groups.append({
            "id": gid, "name": name, "primary_mode_id": primary_id,
            "mode_ids": sorted(member_ids),
            "aliases": sorted(aliases - {name}),
            "start_date": min(m["start_date"] for m in modes),
            "end_date": max(m["end_date"] for m in modes),
            "first_seen": min(m["first_seen"] for m in modes),
            "last_seen": max(m["last_seen"] for m in modes),
            "status": status,
            "priority": "notable" if any(m["priority"] == "notable" for m in modes) else "routine",
        })
    return sorted(groups, key=lambda g: (g["start_date"], g["end_date"], g["id"]))


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

    assign_names(events)
    update_assessments(events, captures)
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
            "name": event["name"],
            "priority": "routine" if event["kind"] in ROUTINE_KINDS else "notable",
            "assessments": event["assessments"][-60:],
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
    groups = group_events(summarized, prior.get("event_groups", []))
    visible_groups = [g for g in groups if g["status"] != "forecast_window_passed"] + sorted(
        (g for g in groups if g["status"] == "forecast_window_passed"),
        key=lambda g: g["end_date"], reverse=True)[:40]
    visible_modes = {mid for g in visible_groups for mid in g["mode_ids"]}
    catalog = {
        "event_groups": groups,
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
            "Support percent counts confirmed sources out of four. Zero requires four clear sources.",
            "Daily deterministic stitched forecasts; model initialization not identified.",
            "Weather-code signals describe the most severe coded daily condition, not every hazard.",
            "Thunderstorm signals are not severe, tornado, hail, or damaging-wind probabilities; flooding requires dedicated hydrologic evidence.",
            "Forecast history is not ground truth; no model skill verdict without observations.",
            "15-day GFS is the longest available collector input. No 17–21 day detection.",
        ],
        "event_groups": visible_groups,
        "events": [x for x in summarized if x["id"] in visible_modes],
    }
    # Never destroy existing event history if no fresh capture is available.
    if captures or not (DEST / "index.json").exists():
        write_json(DEST / "catalog.json", catalog)
        write_json(DEST / "index.json", index)
    print("StormLab pattern watch: processed %s new captures; %s events; %s active signals"
          % (new_count, len(events), sum(x["status"] == "in_latest_capture" for x in summarized)))


if __name__ == "__main__":
    main()
