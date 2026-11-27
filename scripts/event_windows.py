"""Select the active winter-weather window from the named-event catalog.

Storm-specific pipelines (matched-hour blend, rapid-refresh shadow inference)
follow the current or next named winter event instead of hardcoded dates.

Rules:
- Only non-routine winter modes start a window: accumulating snow and ice.
  Flurries (light_snow) and freezing temperatures alone do not.
- A new window needs a fresh event catalog (status "current") and a winter
  mode in the latest capture starting within LOOKAHEAD_DAYS.
- Once selected, a window is pinned in data/storm/window.json until it ends.
  Its start never moves; its end may only extend (capped at MAX_HOURS) when the
  same named event lengthens. That keeps frozen elapsed-hour forecasts valid.
- A window never starts in the past: if the event is already underway when
  first selected, the window starts at the current hour. Elapsed hours are never
  reconstructed from forecasts that were not captured before they occurred.
"""
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "events" / "index.json"
STATE = ROOT / "data" / "storm" / "window.json"
TZ = ZoneInfo("America/Chicago")
WINTER_KINDS = ("snow", "ice")
LOOKAHEAD_DAYS = 5
MAX_HOURS = 120


def mode_kind(mode_id):
    """'light_snow-20261127' -> 'light_snow'."""
    return mode_id.rsplit("-", 1)[0]


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def winter_candidates(index):
    """Named event groups in the latest capture with snow/ice modes, with the
    local date span of those winter modes only (not rain days in the group)."""
    modes = {e["id"]: e for e in index.get("events", [])}
    out = []
    for g in index.get("event_groups", []):
        if g.get("status") != "in_latest_capture":
            continue
        winter = [modes[m] for m in g.get("mode_ids", [])
                  if m in modes and mode_kind(m) in WINTER_KINDS
                  and modes[m].get("status") == "in_latest_capture"]
        if not winter:
            continue
        out.append(dict(event_id=g["id"], event_name=g.get("name"),
                        mode_ids=sorted(m["id"] for m in winter),
                        kinds=sorted({mode_kind(m["id"]) for m in winter}),
                        start_date=min(m["start_date"] for m in winter),
                        end_date=max(m["end_date"] for m in winter)))
    return sorted(out, key=lambda c: (c["start_date"], c["event_id"]))


def local_midnight(day):
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(), TZ)


def floor_hour(now):
    return now.astimezone(TZ).replace(minute=0, second=0, microsecond=0)


def select(index, now, previous=None):
    """Return the window dict to use now, or None. Pure function for testing."""
    if now.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    candidates = winter_candidates(index) if index else []
    if previous:
        start = dt.datetime.fromisoformat(previous["start"])
        end = dt.datetime.fromisoformat(previous["end"])
        if end > now:
            same = next((c for c in candidates if c["event_id"] == previous["event_id"]), None)
            if same:
                extended = local_midnight(same["end_date"]) + dt.timedelta(days=1)
                extended = min(extended, start + dt.timedelta(hours=MAX_HOURS))
                if extended > end:
                    end = extended
                    previous = dict(previous, end=end.isoformat(), mode_ids=same["mode_ids"],
                                    kinds=same["kinds"], extended_at=now.isoformat())
            return dict(previous, hours=int((end - start).total_seconds() // 3600))
    if not index or index.get("status") != "current":
        return None
    today = now.astimezone(TZ).date()
    for c in candidates:
        start_day = dt.date.fromisoformat(c["start_date"])
        end = local_midnight(c["end_date"]) + dt.timedelta(days=1)
        if end <= now or (start_day - today).days > LOOKAHEAD_DAYS:
            continue
        start = max(local_midnight(c["start_date"]), floor_hour(now))
        end = min(end, start + dt.timedelta(hours=MAX_HOURS))
        if end <= start:
            continue
        return dict(c, start=start.isoformat(), end=end.isoformat(),
                    hours=int((end - start).total_seconds() // 3600),
                    selected_at=now.isoformat(),
                    catalog_generated_at=index.get("generated_at"))
    return None


def current(now=None, persist=False):
    """Active window, honoring and optionally updating the pinned state file."""
    now = now or dt.datetime.now(dt.timezone.utc)
    window = select(read_json(INDEX), now, read_json(STATE))
    if persist and window:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({k: v for k, v in window.items() if k != "hours"}, indent=2) + "\n")
    return window


def local_days(window):
    """Local calendar days touched by the window, in order."""
    start = dt.datetime.fromisoformat(window["start"]).astimezone(TZ)
    end = dt.datetime.fromisoformat(window["end"]).astimezone(TZ) - dt.timedelta(microseconds=1)
    days, d = [], start.date()
    while d <= end.date():
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    return days


if __name__ == "__main__":
    # `python scripts/event_windows.py --active` prints run=true/false for workflow gating.
    w = current()
    if "--active" in sys.argv[1:]:
        print("run=" + ("true" if w else "false"))
    else:
        print(json.dumps(w, indent=2))
