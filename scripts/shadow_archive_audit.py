"""Audit shadow captures without scoring or modifying any forecast or observation."""
import datetime as dt
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def timestamp(value):
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("Missing timezone")
    return t.astimezone(dt.timezone.utc)

def classify(capture, window):
    # Completion time, not model initialization or capture start, is conservative.
    try:
        issued = timestamp(capture["generated_at"])
        start = timestamp(window["valid_start_utc"])
        end = timestamp(window["valid_end_utc"])
        if end - start != dt.timedelta(hours=24):
            return "invalid_interval"
    except (KeyError, ValueError, TypeError, AttributeError):
        return "invalid_timestamp"
    if window.get("status") != "research_only":
        return "incomplete"
    if issued >= end:
        return "after_window"
    if issued >= start:
        return "during_window"
    mappings = window.get("mappings", [])
    if not mappings or any(not m.get("model_artifact", {}).get("sha256") for m in mappings):
        return "missing_artifact_provenance"
    return "pre_window_candidate"

def audit(paths):
    counts = Counter()
    candidates = set()
    invalid = []
    for path in sorted(paths):
        try:
            capture = json.loads(path.read_text())
            windows = capture["windows"]
            if not isinstance(windows, list):
                raise ValueError("Invalid windows")
        except (OSError, ValueError, KeyError, TypeError) as e:
            invalid.append({"file": path.name, "error": str(e)})
            continue
        for window in windows:
            category = classify(capture, window)
            counts[category] += 1
            if category == "pre_window_candidate":
                for mapping in window["mappings"]:
                    candidates.add((window["initialization_utc"], window["lead_hours"],
                        mapping["label_mapping"], mapping["model_artifact"]["sha256"]))
    return dict(capture_files=len(paths), window_counts=dict(counts),
        unique_pre_window_model_mapping_candidates=len(candidates),
        invalid_files=invalid, operational_ready=False,
        note="Candidates are not scored forecasts. Completion timestamp is self-reported; independent publication timing, observation alignment, point-level range flags and verification remain required.")

if __name__ == "__main__":
    paths = list((ROOT / "data/regional_live_v2/captures").glob("*.json"))
    print(json.dumps(audit(paths), indent=2))
