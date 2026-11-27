"""Clock-independent event-total verification."""
import datetime as dt
import unittest
from scripts import event_totals as et

def snap(values):
    """values: {town: {model: {date: inches}}}"""
    return {"locations":{t:{m:{"daily":{"time":list(d),"snowfall_sum":list(d.values()),"precipitation_sum":list(d.values())},
            "units":{"snowfall_sum":"inch","precipitation_sum":"inch"}} for m,d in ms.items()} for t,ms in values.items()}}

def uniform(days,town="Wentzville"):
    return {town:{m:dict(days) for m in et.MODELS}}

CASE={"start_date":"2026-12-02","end_date":"2026-12-02"}
DRY_BRACKET={"2026-11-30":0,"2026-12-01":0,"2026-12-02":5.0,"2026-12-03":0,"2026-12-04":0}
STATION=[{"station_id":"ACIS:1","name":"Test COOP","nearest_town":"Wentzville","nearest_town_distance_mi":4.0}]

def reports(values,quality=None,clock="7"):
    return [{"station_id":"ACIS:1","reported_date":d,"snowfall_in":v,"snow_quality":(quality or {}).get(d,"measured"),
             "snow_observation_clock_raw":clock} for d,v in values.items()]

def capture_finder(captures):
    """captures: list of (utc stamp, snapshot dict); returns newest before local midnight of start."""
    def find(case,start=None):
        start=start or dt.date.fromisoformat(case["start_date"])
        cutoff=dt.datetime.combine(start,dt.time(),et_tz()).astimezone(dt.timezone.utc)
        ok=[(s,i) for i,(s,_) in enumerate(captures) if s<cutoff]
        return max(ok) if ok else None
    return find,(lambda i:captures[i][1])

def et_tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/Chicago")

def score(captures,obs,stations=STATION,case=CASE,kind="snow"):
    find,load=capture_finder(captures)
    return et.score_event(case,kind,stations,obs,["Wentzville"],find,load)

PRE=dt.datetime(2026,12,1,12,tzinfo=dt.timezone.utc)

class EventTotalTests(unittest.TestCase):
    def test_reports_s_to_e_plus_1_cover_any_clock(self):
        # Morning observer: snow split across reported dates 12-02 and 12-03 still totals 5".
        r=score([(PRE,snap(uniform(DRY_BRACKET)))],reports({"2026-12-02":2.0,"2026-12-03":3.0}))
        self.assertEqual(r["status"],"scored")
        self.assertEqual(r["verification_window"]["observed_report_dates"],{"start":"2026-12-02","end":"2026-12-03"})
        pair=r["station_pairs"][0]
        self.assertEqual(pair["observed_in"],5.0)
        self.assertEqual(pair["forecasts"]["GFS"]["error_in"],0.0)
        self.assertTrue(pair["forecasts"]["model_mean"]["clock_independent"])
        self.assertEqual(r["scores"]["model_mean"],{"n":1,"mae_in":0.0,"bias_in":0.0,"rmse_in":0.0})

    def test_window_widens_with_capture_reselected_before_new_start(self):
        wet_before={**DRY_BRACKET,"2026-12-01":2.0}
        early=dt.datetime(2026,11,30,12,tzinfo=dt.timezone.utc)
        caps=[(early,snap(uniform({"2026-11-29":0,"2026-11-30":0,"2026-12-01":1.5,"2026-12-02":4.0,"2026-12-03":0}))),
              (PRE,snap(uniform(wet_before)))]
        r=score(caps,reports({"2026-12-01":1.0,"2026-12-02":3.0,"2026-12-03":0.5}))
        self.assertEqual(r["verification_window"]["start"],"2026-12-01")
        self.assertEqual(r["forecast_capture"]["captured_at"],early.isoformat())
        self.assertEqual(r["station_pairs"][0]["forecasts"]["GFS"]["forecast_in"],5.5)
        self.assertEqual(r["station_pairs"][0]["observed_in"],4.5)

    def test_unbracketable_event_not_scored(self):
        wet={d:3.0 for d in ("2026-11-27","2026-11-28","2026-11-29","2026-11-30","2026-12-01","2026-12-02","2026-12-03","2026-12-04","2026-12-05","2026-12-06")}
        r=score([(dt.datetime(2026,11,26,12,tzinfo=dt.timezone.utc),snap(uniform(wet)))],[])
        self.assertEqual(r["status"],"not_scored");self.assertEqual(r["reason"],"not_bracketed")

    def test_missing_flagged_or_clock_zero_reports_disqualify(self):
        caps=[(PRE,snap(uniform(DRY_BRACKET)))]
        r=score(caps,reports({"2026-12-02":4.0}))
        self.assertEqual(r["status"],"not_scored");self.assertTrue(r["stations_excluded"][0]["reason"].startswith("missing_report"))
        r=score(caps,reports({"2026-12-02":4.0,"2026-12-03":0.0},quality={"2026-12-03":"accumulated_interval_unknown"}))
        self.assertEqual(r["status"],"not_scored")
        r=score(caps,reports({"2026-12-02":4.0,"2026-12-03":0.0},clock="0"))
        self.assertTrue(r["stations_excluded"][0]["reason"].startswith("observation_clock_zero"))

    def test_trace_is_zero_with_upper_bound_not_measured_zero(self):
        r=score([(PRE,snap(uniform(DRY_BRACKET)))],reports({"2026-12-02":4.0,"2026-12-03":None},quality={"2026-12-03":"trace"}))
        p=r["station_pairs"][0]
        self.assertEqual((p["observed_in"],p["observed_upper_in"],p["trace_days"]),(4.0,4.05,1))

    def test_distant_station_and_no_prior_capture_not_scored(self):
        far=[dict(STATION[0],nearest_town_distance_mi=15.0)]
        r=score([(PRE,snap(uniform(DRY_BRACKET)))],reports({"2026-12-02":4.0,"2026-12-03":0}),stations=far)
        self.assertEqual(r["status"],"not_scored")
        late=dt.datetime(2026,12,2,12,tzinfo=dt.timezone.utc)
        self.assertEqual(score([(late,snap(uniform(DRY_BRACKET)))],[])["reason"],"no_prior_capture")

    def test_adjacent_forecast_flags_pair_not_clock_independent(self):
        # Mean adjacent forecast below the widening tolerance, but one model's is large relative to its total.
        days={**DRY_BRACKET,"2026-12-02":0.5}
        vals=uniform(days);vals["Wentzville"]["GEM"]={**days,"2026-12-03":0.35}
        r=score([(PRE,snap(vals))],reports({"2026-12-02":0.4,"2026-12-03":0.1}))
        self.assertFalse(r["station_pairs"][0]["forecasts"]["GEM"]["clock_independent"])
        self.assertNotIn("GEM",r["scores"])
        self.assertIn("GFS",r["scores"])

    def test_threshold_contingency(self):
        r=score([(PRE,snap(uniform(DRY_BRACKET)))],reports({"2026-12-02":0.5,"2026-12-03":0.2}))
        self.assertEqual(r["threshold_contingency"]["GFS@1.0"],{"hits":0,"misses":0,"false_alarms":1,"correct_nulls":0})
        self.assertEqual(r["threshold_contingency"]["GFS@4.0"]["false_alarms"],1)

    def test_negative_daily_values(self):
        s=snap(uniform({"2026-12-01":-0.028,"2026-12-02":-1.0}))
        self.assertEqual(et.daily(s,"Wentzville","GFS","snowfall_sum"),{"2026-12-01":0.0})

if __name__=="__main__":unittest.main()
