"""Unit checks for local, source-traceable StormLab event intelligence."""
import datetime as dt
import json
import pathlib
import tempfile
import unittest
from zoneinfo import ZoneInfo

from scripts import event_watch as watch


def snapshot(stamp, snow=3.2, date=None, models=("GFS", "ECMWF")):
    when = watch.parse_time(stamp).astimezone(watch.CT).date()
    day = date or (when + dt.timedelta(days=5)).isoformat()
    return {
        "retrieved_at": stamp, "errors": [],
        "locations": {
            "Wentzville": {
                name: {
                    "daily": {
                        "time": [day],
                        "snowfall_sum": [snow],
                        "precipitation_sum": [0.4],
                        "wind_gusts_10m_max": [20],
                        "temperature_2m_min": [30],
                        "temperature_2m_max": [45],
                    },
                    "units": {
                        "snowfall_sum": "inch",
                        "precipitation_sum": "inch",
                        "wind_gusts_10m_max": "mp/h",
                        "temperature_2m_min": "°F",
                        "temperature_2m_max": "°F",
                    },
                } for name in models
            }
        }
    }


class EventWatchTests(unittest.TestCase):
    def test_detects_regional_snow_signal_without_fake_probability(self):
        stamp = dt.datetime.now(watch.UTC).isoformat()
        out = watch.captured_signals(snapshot(stamp))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "snow")
        self.assertEqual(out[0]["same_place_day_support"], 2)
        self.assertEqual(out[0]["models"], ["ECMWF", "GFS"])
        self.assertEqual(out[0]["wentzville"]["triggering_samples"], 2)
        self.assertNotIn("probability", out[0])

    def test_does_not_treat_missing_or_bad_units_as_zero_or_trigger(self):
        stamp = dt.datetime.now(watch.UTC).isoformat()
        s = snapshot(stamp, snow=3.2)
        s["locations"]["Wentzville"]["GFS"]["units"]["snowfall_sum"] = "mystery"
        s["locations"]["Wentzville"]["ECMWF"]["daily"]["snowfall_sum"] = [None]
        self.assertEqual(watch.captured_signals(s), [])

    def test_conversion_and_cold_temperature(self):
        self.assertAlmostEqual(watch.normalized(2.54, "cm", "in"), 1)
        self.assertAlmostEqual(watch.normalized(0, "°C", "F"), 32)
        self.assertAlmostEqual(watch.normalized(48.28, "km/h", "mph"), 30, delta=.01)

    def test_idempotent_aggregation_and_persistent_tracking(self):
        with tempfile.TemporaryDirectory() as root:
            old_data, old_dest = watch.DATA, watch.DEST
            try:
                watch.DATA = pathlib.Path(root)
                watch.DEST = watch.DATA / "events"
                now = dt.datetime.now(watch.UTC)
                a = snapshot((now-dt.timedelta(hours=2)).isoformat(), snow=3.1)
                b = snapshot((now-dt.timedelta(hours=1)).isoformat(), snow=4.1)
                (watch.DATA / "history.json").write_text(
                    json.dumps([a,b]), encoding="utf-8")
                watch.main()
                catalog = json.loads((watch.DEST / "catalog.json").read_text())
                index = json.loads((watch.DEST / "index.json").read_text())
                self.assertEqual(len(catalog["events"]), 1)
                self.assertEqual(len(catalog["events"][0]["timeline"]), 2)
                self.assertEqual(len(index["events"]), 1)
                self.assertEqual(index["events"][0]["snapshots_seen"], 2)
                self.assertEqual(index["events"][0]["observation_status"], "not_connected")
                watch.main()
                catalog2 = json.loads((watch.DEST / "catalog.json").read_text())
                self.assertEqual(len(catalog2["events"][0]["timeline"]), 2)
                self.assertEqual(len(catalog2["processed_captures"]), 2)
            finally:
                watch.DATA, watch.DEST = old_data, old_dest

    def test_temporary_missing_model_does_not_claim_corroboration(self):
        stamp = dt.datetime.now(watch.UTC).isoformat()
        out = watch.captured_signals(snapshot(stamp, models=("GFS",)))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["same_place_day_support"], 1)


if __name__ == "__main__":
    unittest.main()
