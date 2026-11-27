"""Clock-independent event-total verification of frozen pre-event forecasts.

Problem: station daily reports cover a 24-hour period ending at the station's
observation time (often ~7 AM), and that time is unverified. Model days are
local midnight to midnight.

Method (no observation clock is assumed):
  If report D covers (D-1 at clock c, D at clock c] with 0 < c <= 24h, then
  reports dated S..E+1 together cover (S-1+c, E+1+c]. That interval always
  contains the forecast window [S 00:00, E+1 00:00), for any c. The overhang
  totals exactly 24 hours, split before S and after E+1.
  So the observed sum over S..E+1 equals the window total plus whatever fell
  in that 24h overhang. The overhang lies inside forecast days S-1 and E+1.
  When the forecast puts (near) zero on those adjacent days, the comparison
  cannot depend on the clock, and AMBIGUITY = F(S-1) + F(E+1) bounds what the
  clock could change.

Window: start from the catalog event days, then widen (up to MAX_EXPAND days
per side) while the 4-model mean forecast at any town exceeds the adjacency
tolerance on an adjacent day. Widening uses ONLY the frozen pre-event capture,
never observations, and the capture is re-selected to precede the widened
start. A window that cannot be bracketed is not scored.

Observations: every report S..E+1 must be measured, or a trace (counted as 0
with a +TRACE_UPPER_IN upper bound per trace day). Missing, flagged,
accumulated or out-of-range reports disqualify the station. Reports with an
observation clock of 0 (a period ending at the start of the date) are excluded.
Only stations within MAX_STATION_MI of a forecast town are scored, against that
town's forecast. A station is a point, not the town or an area average.
"""
from __future__ import annotations
import datetime as dt
import math
import statistics as st

MODELS = ("GFS", "ECMWF", "GEM", "ICON")
VARIABLES = {"snow": ("snowfall_sum", "snowfall_in", "snow_quality", "snow_observation_clock_raw"),
             "precipitation": ("precipitation_sum", "precipitation_in", "precip_quality", "precip_observation_clock_raw")}
ADJ_TOL_IN = {"snow": 0.1, "precipitation": 0.01}
ADJ_TOL_FRAC = 0.1
MAX_EXPAND = 2
MAX_STATION_MI = 10.0
TRACE_UPPER_IN = {"snow": 0.05, "precipitation": 0.005}
NEG_TOLERANCE = -0.05
THRESHOLDS = {"snow": (1.0, 4.0), "precipitation": (0.25, 1.0)}
METHOD_VERSION = 1


def day(s):
    return dt.date.fromisoformat(s)


def daily(snapshot, town, model, field):
    """{local date: inches} from one frozen capture; tiny negatives -> 0."""
    info = (snapshot.get("locations") or {}).get(town, {}).get(model)
    if not isinstance(info, dict):
        return {}
    d = info.get("daily") or {}
    unit = (info.get("units") or {}).get(field)
    scale = {"inch": 1.0, "in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4}.get(unit)
    if scale is None:
        return {}
    out = {}
    for t, v in zip(d.get("time") or [], d.get(field) or []):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < NEG_TOLERANCE:
            continue
        out[t] = max(0.0, v) * scale
    return out


def forecast_table(snapshot, towns, field):
    return {town: {m: daily(snapshot, town, m, field) for m in MODELS} for town in towns}


def model_mean(table, town, date):
    vals = [v[date] for v in table[town].values() if date in v]
    return st.mean(vals) if len(vals) >= 2 else None


def window_sum(series, start, end):
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    if not all(d in series for d in days):
        return None
    return sum(series[d] for d in days)


def bracket(table, kind, start, end):
    """One widening step: return (start, end, reason). Unchanged dates mean bracketed."""
    tol = ADJ_TOL_IN[kind]
    before = (start - dt.timedelta(days=1)).isoformat()
    after = (end + dt.timedelta(days=1)).isoformat()
    grow_start = grow_end = False
    for town in table:
        b, a = model_mean(table, town, before), model_mean(table, town, after)
        if b is None or a is None:
            return start, end, "adjacent_day_forecast_missing"
        grow_start |= b > tol
        grow_end |= a > tol
    return (start - dt.timedelta(days=1) if grow_start else start,
            end + dt.timedelta(days=1) if grow_end else end, None)


def resolve_window(case, kind, towns, find_capture, load_snapshot):
    """Alternate capture selection and one-step widening until the newest capture
    made before the window start shows near-zero forecast on both adjacent days."""
    field = VARIABLES[kind][0]
    first, last = day(case["start_date"]), day(case["end_date"])
    start, end = first, last
    for _ in range(2 * MAX_EXPAND + 2):
        found = find_capture(case, start)
        if not found:
            return dict(status="no_prior_capture", start=start, end=end)
        stamp, path = found
        table = forecast_table(load_snapshot(path), towns, field)
        s2, e2, reason = bracket(table, kind, start, end)
        info = dict(start=start, end=end, captured_at=stamp, path=path)
        if reason:
            return dict(info, status=reason)
        if s2 == start and e2 == end:
            return dict(info, status="ok", table=table)
        if (first - s2).days > MAX_EXPAND or (e2 - last).days > MAX_EXPAND:
            return dict(info, status="not_bracketed", start=s2, end=e2)
        start, end = s2, e2
    return dict(status="not_bracketed", start=start, end=end)


def station_total(kind, reports, start, end):
    """Observed sum over reported dates start..end+1, or (None, reason)."""
    _, value_key, quality_key, clock_key = VARIABLES[kind]
    total, traces, used = 0.0, 0, []
    for i in range((end - start).days + 2):
        d = (start + dt.timedelta(days=i)).isoformat()
        r = reports.get(d)
        if r is None:
            return None, "missing_report_" + d
        q = r.get(quality_key)
        if str(r.get(clock_key)).strip() == "0":
            return None, "observation_clock_zero_" + d
        if q == "trace":
            traces += 1
        elif q == "measured" and isinstance(r.get(value_key), (int, float)):
            total += r[value_key]
        else:
            return None, f"{q or 'unusable'}_{d}"
        used.append(d)
    return dict(observed_in=round(total, 3), observed_upper_in=round(total + traces * TRACE_UPPER_IN[kind], 3),
                trace_days=traces, reported_dates=used), None


def summarize(errors):
    if not errors:
        return None
    return dict(n=len(errors), mae_in=round(st.mean(abs(e) for e in errors), 3),
                bias_in=round(st.mean(errors), 3),
                rmse_in=round(math.sqrt(st.mean(e * e for e in errors)), 3))


def score_event(case, kind, stations, observations, towns, find_capture, load_snapshot):
    """Event-total verification record for one catalog case (snow or precipitation)."""
    base = dict(method="clock_independent_event_total", method_version=METHOD_VERSION, variable=kind,
                catalog_dates=dict(start=case["start_date"], end=case["end_date"]))
    w = resolve_window(case, kind, towns, find_capture, load_snapshot)
    base["verification_window"] = dict(start=w["start"].isoformat(), end=w["end"].isoformat(),
                                       observed_report_dates=dict(start=w["start"].isoformat(),
                                                                  end=(w["end"] + dt.timedelta(days=1)).isoformat()))
    if "captured_at" in w:
        base["forecast_capture"] = dict(captured_at=w["captured_at"].isoformat(), snapshot_path=str(w["path"]))
    if w["status"] != "ok":
        return dict(base, status="not_scored", reason=w["status"])
    table = w["table"]
    start, end = w["start"], w["end"]
    before = (start - dt.timedelta(days=1)).isoformat()
    after = (end + dt.timedelta(days=1)).isoformat()
    by_station = {}
    for o in observations:
        by_station.setdefault(o["station_id"], {})[o["reported_date"]] = o
    pairs, excluded = [], []
    errors = {m: [] for m in MODELS + ("model_mean",)}
    hits = {}
    for s in stations:
        town = s.get("nearest_town")
        if town not in table or s.get("nearest_town_distance_mi", 99) > MAX_STATION_MI:
            continue
        obs, why = station_total(kind, by_station.get(s["station_id"], {}), start, end)
        if obs is None:
            excluded.append(dict(station_id=s["station_id"], reason=why))
            continue
        forecasts = {}
        for m, series in table[town].items():
            core = window_sum(series, start, end)
            if core is None or before not in series or after not in series:
                continue
            amb = series[before] + series[after]
            forecasts[m] = dict(forecast_in=round(core, 3), clock_ambiguity_in=round(amb, 3),
                                error_in=round(core - obs["observed_in"], 3),
                                clock_independent=amb <= max(ADJ_TOL_IN[kind], ADJ_TOL_FRAC * core))
        if len(forecasts) >= 2:
            mean = st.mean(f["forecast_in"] for f in forecasts.values())
            amb = st.mean(f["clock_ambiguity_in"] for f in forecasts.values())
            forecasts["model_mean"] = dict(forecast_in=round(mean, 3), clock_ambiguity_in=round(amb, 3),
                                           error_in=round(mean - obs["observed_in"], 3),
                                           clock_independent=amb <= max(ADJ_TOL_IN[kind], ADJ_TOL_FRAC * mean))
        for m, f in forecasts.items():
            if f["clock_independent"]:
                errors[m].append(f["error_in"])
                for t in THRESHOLDS[kind]:
                    k = (m, t)
                    h = hits.setdefault(k, dict(hits=0, misses=0, false_alarms=0, correct_nulls=0))
                    fo, ob = f["forecast_in"] >= t, obs["observed_in"] >= t
                    h["hits" if fo and ob else "misses" if ob else "false_alarms" if fo else "correct_nulls"] += 1
        pairs.append(dict(station_id=s["station_id"], station_name=s.get("name"), town=town,
                          distance_mi=s.get("nearest_town_distance_mi"), **obs, forecasts=forecasts))
    scores = {m: summarize(e) for m, e in errors.items() if e}
    contingency = {f"{m}@{t}": h for (m, t), h in sorted(hits.items())}
    status = "scored" if scores else "not_scored"
    return dict(base, status=status, reason=None if scores else "no_complete_station_reports_within_%g_mi" % MAX_STATION_MI,
                stations_scored=len(pairs), stations_excluded=excluded[:60], scores=scores,
                threshold_contingency=contingency, station_pairs=pairs[:80],
                caveats=["Station point reports compared with the forecast for the nearest town (<= %g mi); not an area average." % MAX_STATION_MI,
                         "Clock independence relies on near-zero forecast on the adjacent days; residual bound is reported per pair.",
                         "Single event, correlated stations: not a skill estimate or calibration result."])
