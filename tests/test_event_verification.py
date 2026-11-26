"""Fail-closed tests for independent station observation verification."""
import datetime as dt
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from scripts import event_verification as v


class VerificationTests(unittest.TestCase):
    def test_observation_quality_does_not_invent_zero(self):
        self.assertEqual(v.decode_amount("M")[1],"missing")
        self.assertEqual(v.decode_amount("T")[1],"trace")
        self.assertEqual(v.decode_amount("1.2A")[1],"accumulated_interval_unknown")
        self.assertEqual(v.decode_amount(["2.5","0700",""])[0],2.5)
        self.assertEqual(v.decode_amount(["2.5","0700","Q"])[1],"flagged")
        self.assertEqual(v.decode_amount("-0.1")[1],"out_of_range")
        self.assertEqual(v.decode_amount("2.25")[0],2.25)

    def test_station_daily_record_preserves_time_provenance(self):
        start=dt.date(2026,11,24)
        end=dt.date(2026,11,26)
        payload={"data":[{
            "meta":{"uid":1245,"name":"TEST STATION","state":"MO",
                    "ll":[-90.8529,38.8114],"sids":["US1MOST0020 10"]},
            "data":[
                ["2026-11-25",["3.5","0700",""],["0.4","0700",""]],
                ["2026-11-26",["T","0700",""],["M","0700",""]],
                ["2026-11-24",["2.0A","0700",""],["0.3","0700",""]],
            ],
        }]}
        stations,obs=v.normalize_provider(payload,start,end)
        self.assertEqual(len(stations),1)
        self.assertEqual(stations[0]["nearest_town"],"Wentzville")
        self.assertEqual(len(obs),3)
        self.assertEqual(obs[0]["snowfall_in"],3.5)
        self.assertEqual(obs[0]["snow_observation_clock_raw"],"0700")
        self.assertFalse(obs[0]["observation_interval_verified"])
        self.assertIsNone(obs[1]["snowfall_in"])
        self.assertEqual(obs[1]["snow_quality"],"trace")
        self.assertEqual(obs[2]["snow_quality"],"accumulated_interval_unknown")

    def test_real_acis_multistation_arrays_map_dates_by_exact_range(self):
        start=dt.date(2026,11,23)
        end=dt.date(2026,11,25)
        raw={"data":[{"meta":{"uid":9,"name":"SAMPLE","state":"MO",
                "ll":[-90.8529,38.8114],"sids":["US1MOST0012 10"]},
            "data":[
                [["M",-1,"M"],["0.04",7," "]],
                [["0.0",8," "],["0.00",8," "]],
                [["1.5",8," "],["0.20",8," "]],
            ]}]}
        stations,observations=v.normalize_provider(raw,start,end)
        self.assertEqual(len(stations),1)
        self.assertEqual(len(observations),3)
        self.assertEqual([o["reported_date"] for o in observations],
            ["2026-11-23","2026-11-24","2026-11-25"])
        self.assertEqual(observations[0]["snow_quality"],"missing")
        self.assertEqual(observations[0]["precipitation_in"],.04)
        self.assertEqual(observations[1]["snowfall_in"],0.0)
        self.assertEqual(observations[2]["snowfall_in"],1.5)
        self.assertEqual(observations[2]["date_mapping"],"requested_range_index")
        self.assertFalse(observations[2]["observation_interval_verified"])
        # A truncated or otherwise incomplete range must never be assigned
        # dates by pretending it starts on the first requested day.
        raw["data"][0]["data"].pop()
        _,incomplete=v.normalize_provider(raw,start,end)
        self.assertEqual(incomplete,[])

    def test_verification_holds_score_even_with_station_report(self):
        case={"id":"snow-20261125","kind":"snow","title":"Snowfall potential",
            "start_date":"2026-11-25","end_date":"2026-11-26","timeline":[]}
        station={"station_id":"ACIS:1245","name":"TEST STATION","nearest_town":"Wentzville",
                 "nearest_town_distance_mi":1.5}
        obs={"station_id":"ACIS:1245","reported_date":"2026-11-26",
             "snowfall_in":5.0,"snow_quality":"measured",
             "snow_observation_clock_raw":"0700","precip_quality":"missing"}
        done=v.build_reports([case],[station],[obs],dt.date(2026,11,28))[0]
        self.assertEqual(done["observation_status"],"observations_available_timing_review_required")
        self.assertEqual(done["daily_report_count"],1)
        self.assertIsNone(done["actual_observed_storm_total_in"])
        self.assertIsNone(done["forecast_error_in"])
        self.assertEqual(done["verification_status"],"not_scored")
        self.assertFalse(done["station_daily_reports"][0]["measurement_interval_verified"])

    def test_forecast_reference_uses_only_pre_event_capture(self):
        capture=dt.datetime(2026,11,24,18,57,10,tzinfo=dt.timezone.utc)
        future=dt.datetime(2026,11,25,18,tzinfo=dt.timezone.utc)
        case={"id":"snow-20261125","kind":"snow","title":"Snowfall potential",
              "start_date":"2026-11-25","end_date":"2026-11-26",
              "timeline":[{"captured_at":capture.isoformat()},
                          {"captured_at":future.isoformat()}]}
        with tempfile.TemporaryDirectory() as root:
            old=v.DATA
            try:
                v.DATA=pathlib.Path(root)
                target=v.DATA/"snapshots"/"2026-11"/"20261124T185710Z.json"
                target.parent.mkdir(parents=True)
                target.write_text(json.dumps({"locations":{
                    "Wentzville":{"GFS":{
                        "units":{"snowfall_sum":"inch"},
                        "daily":{"time":["2026-11-25","2026-11-26"],
                                 "snowfall_sum":[1.0,2.0]}}}}}))
                ref=v.forecast_reference(case)
                self.assertEqual(ref["status"],"pre_event_capture")
                self.assertEqual(len(ref["daily_forecasts"]),2)
                self.assertEqual(ref["captured_at"],capture.isoformat())
                self.assertTrue(ref["snapshot_path"].endswith("20261124T185710Z.json"))
            finally:
                v.DATA=old

    def test_future_events_are_not_declared_observed(self):
        case={"id":"snow-20261127","kind":"snow","title":"Snowfall potential",
              "start_date":"2026-11-27","end_date":"2026-11-28","timeline":[]}
        result=v.build_reports([case],[],[],dt.date(2026,11,25))[0]
        self.assertEqual(result["observation_status"],"event_window_not_complete")
        self.assertIsNone(result["forecast_error_in"])

    def test_station_location_not_confused_with_town(self):
        self.assertLess(v.distance_miles((38.8114,-90.8529),(38.8114,-90.8529)),.001)
        self.assertGreater(v.distance_miles((38.8114,-90.8529),(38.6270,-90.1994)),30)

    def test_prior_station_days_persist_and_new_corrections_replace_them(self):
        station={"station_id":"ACIS:111","name":"TEST"}
        old=[{"station_id":"ACIS:111","reported_date":"2026-11-23",
              "snowfall_in":1.0},{"station_id":"ACIS:111",
              "reported_date":"2026-11-24","snowfall_in":2.0}]
        new=[{"station_id":"ACIS:111","reported_date":"2026-11-24",
              "snowfall_in":2.5},{"station_id":"ACIS:111",
              "reported_date":"2026-11-25","snowfall_in":3.0}]
        stations,obs=v.retain_recent_measurements([station],old,[station],
                  new,dt.date(2026,11,26))
        self.assertEqual(len(stations),1)
        self.assertEqual(len(obs),3)
        by_date={r["reported_date"]:r["snowfall_in"] for r in obs}
        self.assertEqual(by_date["2026-11-23"],1.0)
        self.assertEqual(by_date["2026-11-24"],2.5)
        self.assertEqual(by_date["2026-11-25"],3.0)
        _,trimmed=v.retain_recent_measurements([station],old,[station],new,
                  dt.date(2027,4,1))
        self.assertEqual(trimmed,[])

    def test_acis_provider_url_lists_snow_precip_and_flags(self):
        url=v.provider_request(dt.date(2026,11,24),dt.date(2026,11,26))
        self.assertIn("MultiStnData",url)
        self.assertIn("snow",url)
        self.assertIn("pcpn",url)
        self.assertIn("uid",url)


if __name__=="__main__":
    unittest.main()
