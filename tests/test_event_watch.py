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
                        "temperature_2m_min": [34],
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

    def test_assessment_clear_missing_and_positive(self):
        stamp = dt.datetime.now(watch.UTC).isoformat()
        s = snapshot(stamp, snow=0, models=watch.MODELS)
        s["locations"] = {p: s["locations"]["Wentzville"].copy() for p in watch.PLACES}
        day = s["locations"]["Wentzville"]["GFS"]["daily"]["time"][0]
        self.assertEqual(watch.assess(s, "snow", day, day)["support_pct"], 0)
        del s["locations"]["Wentzville"]["ICON"]
        a = watch.assess(s, "snow", day, day)
        self.assertIsNone(a["support_pct"])
        self.assertEqual(a["possible_support_pct"], 25)
        s["locations"]["Wentzville"]["GFS"]["daily"]["snowfall_sum"] = [3]
        a = watch.assess(s, "snow", day, day)
        self.assertEqual(a["support_pct"], 25)
        self.assertEqual(a["possible_support_pct"], 50)

    def test_watch_name_and_faded_history_persist(self):
        now = dt.datetime.now(watch.UTC)
        a = snapshot((now-dt.timedelta(hours=3)).isoformat(), models=watch.MODELS)
        day = a["locations"]["Wentzville"]["GFS"]["daily"]["time"][0]
        b = snapshot((now-dt.timedelta(hours=2)).isoformat(), snow=0, date=day, models=watch.MODELS)
        c = snapshot((now-dt.timedelta(hours=1)).isoformat(), date=day, models=watch.MODELS)
        for s in (a,b,c):
            s["locations"] = {p: s["locations"]["Wentzville"] for p in watch.PLACES}
        events = []
        watch.upsert(events, watch.captured_signals(a)[0], a["retrieved_at"], set())
        watch.assign_names(events)
        name = events[0]["name"]
        watch.update_assessments(events, [a,b])
        self.assertEqual([p["support_pct"] for p in events[0]["assessments"]], [100,0])
        watch.upsert(events, watch.captured_signals(c)[0], c["retrieved_at"], set())
        watch.assign_names(events)
        watch.update_assessments(events, [a,b,c])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], name)
        self.assertEqual([p["support_pct"] for p in events[0]["assessments"]], [100,0,100])

    def test_expanded_hazards_and_missing_codes(self):
        stamp = dt.datetime.now(watch.UTC).isoformat()
        s = snapshot(stamp, snow=0)
        for m in s["locations"]["Wentzville"].values():
            m["daily"].update(rain_sum=[.05], showers_sum=[.02], weather_code=[95],
                              apparent_temperature_max=[106], apparent_temperature_min=[-2])
            m["units"].update(rain_sum="inch", showers_sum="inch", weather_code="wmo code",
                              apparent_temperature_max="°F", apparent_temperature_min="°F")
        kinds = {x["kind"] for x in watch.captured_signals(s)}
        self.assertEqual(kinds, {"rain", "showers", "thunderstorm", "heat_stress", "cold_stress"})
        self.assertEqual(watch.screen_value("ice", 66, "wmo code"), 1)
        self.assertEqual(watch.screen_value("ice", 61, "wmo code"), 0)
        self.assertIsNone(watch.screen_value("ice", None, "wmo code"))
        self.assertIsNone(watch.screen_value("ice", 66, "unknown"))
        self.assertIsNone(watch.screen_value("thunderstorm", 98, "wmo code"))

    def test_date_groups_preserve_mode_ids_and_parent_name(self):
        def mode(id, name, kind, start, end, status="in_latest_capture"):
            return dict(id=id, name=name, kind=kind, start_date=start, end_date=end,
                        first_seen="2026-11-24T12:00:00Z", last_seen="2026-11-25T12:00:00Z",
                        priority="notable", status=status)
        snow = mode("snow-1", "Drift", "snow", "2026-11-26", "2026-11-26")
        rain = mode("rain-1", "Alder", "precipitation", "2026-11-26", "2026-11-26")
        wind = mode("wind-1", "Gale", "wind", "2026-12-01", "2026-12-01")
        groups = watch.group_events([snow,rain,wind])
        self.assertEqual(len(groups), 2)
        group = groups[0]
        self.assertEqual(group["name"], "Drift")
        self.assertEqual(group["mode_ids"], ["rain-1","snow-1"])
        self.assertEqual(group["aliases"], ["Alder"])
        rain["status"] = "not_in_latest_capture"
        updated = watch.group_events([snow,rain,wind], groups)
        self.assertEqual(updated[0]["id"], group["id"])
        self.assertEqual(updated[0]["status"], "in_latest_capture")
        rain["start_date"] = rain["end_date"] = "2026-11-28"
        split = watch.group_events([rain,wind,snow], updated)
        self.assertEqual(len(split), 3)
        self.assertEqual(split[0]["id"], group["id"])
        self.assertEqual(len({g["name"] for g in split}), 3)

    def test_temporary_missing_model_does_not_claim_corroboration(self):
        stamp = dt.datetime.now(watch.UTC).isoformat()
        out = watch.captured_signals(snapshot(stamp, models=("GFS",)))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["same_place_day_support"], 1)


if __name__ == "__main__":
    unittest.main()
