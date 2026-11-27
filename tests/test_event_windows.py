import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import event_windows as ew
import storm_blend

UTC=dt.timezone.utc

def index(modes,status="current",group_status="in_latest_capture"):
    events=[dict(id=f"{k}-{s.replace('-','')}",start_date=s,end_date=e,status="in_latest_capture") for k,s,e in modes]
    return dict(status=status,generated_at="2026-12-01T00:00:00+00:00",events=events,
        event_groups=[dict(id="event-x",name="Frost",status=group_status,mode_ids=[e["id"] for e in events])])

class SelectTests(unittest.TestCase):
    def test_upcoming_snow_uses_local_midnights_and_winter_days_only(self):
        idx=index([("snow","2026-12-03","2026-12-04"),("rain","2026-12-02","2026-12-06")])
        w=ew.select(idx,dt.datetime(2026,12,1,12,tzinfo=UTC))
        self.assertEqual(w["event_name"],"Frost")
        self.assertEqual(w["start"],"2026-12-03T00:00:00-06:00")
        self.assertEqual(w["end"],"2026-12-05T00:00:00-06:00")
        self.assertEqual(w["hours"],48)
        self.assertEqual(w["mode_ids"],["snow-20261203"])

    def test_routine_flurries_and_rain_do_not_open_window(self):
        idx=index([("light_snow","2026-12-02","2026-12-02"),("rain","2026-12-02","2026-12-03")])
        self.assertIsNone(ew.select(idx,dt.datetime(2026,12,1,12,tzinfo=UTC)))

    def test_ice_opens_window(self):
        idx=index([("ice","2026-12-02","2026-12-02")])
        self.assertEqual(ew.select(idx,dt.datetime(2026,12,1,12,tzinfo=UTC))["kinds"],["ice"])

    def test_stale_catalog_or_far_event_does_not_open_window(self):
        now=dt.datetime(2026,12,1,12,tzinfo=UTC)
        self.assertIsNone(ew.select(index([("snow","2026-12-02","2026-12-02")],status="stale"),now))
        self.assertIsNone(ew.select(index([("snow","2026-12-09","2026-12-09")]),now))
        self.assertIsNone(ew.select(index([("snow","2026-12-02","2026-12-02")],group_status="not_in_latest_capture"),now))

    def test_underway_event_never_starts_in_the_past(self):
        now=dt.datetime(2026,12,2,15,40,tzinfo=UTC)  # 09:40 CST
        w=ew.select(index([("snow","2026-12-02","2026-12-02")]),now)
        self.assertEqual(w["start"],"2026-12-02T09:00:00-06:00")
        self.assertEqual(w["hours"],15)

    def test_pinned_window_keeps_start_extends_end_with_cap(self):
        now=dt.datetime(2026,12,1,12,tzinfo=UTC)
        first=ew.select(index([("snow","2026-12-02","2026-12-02")]),now)
        later=dt.datetime(2026,12,2,12,tzinfo=UTC)
        # Catalog dropped to stale and the event lengthened by a lot.
        w=ew.select(index([("snow","2026-12-02","2026-12-20")],status="stale"),later,first)
        self.assertEqual(w["start"],first["start"])
        self.assertEqual(w["hours"],ew.MAX_HOURS)
        # Event vanished from the catalog: window persists unchanged.
        w=ew.select(index([]),later,first)
        self.assertEqual((w["start"],w["end"]),(first["start"],first["end"]))

    def test_ended_window_is_replaced(self):
        old=dict(event_id="event-old",event_name="Drift",start="2026-11-25T00:00:00-06:00",end="2026-11-27T00:00:00-06:00",mode_ids=[],kinds=["snow"])
        now=dt.datetime(2026,12,1,12,tzinfo=UTC)
        self.assertIsNone(ew.select(index([]),now,old))
        self.assertEqual(ew.select(index([("snow","2026-12-02","2026-12-02")]),now,old)["event_id"],"event-x")

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):ew.select(index([]),dt.datetime(2026,12,1))

class HelperTests(unittest.TestCase):
    def test_local_days_and_blend_hours(self):
        w=dict(start="2026-11-25T00:00:00-06:00",end="2026-11-27T00:00:00-06:00")
        self.assertEqual(ew.local_days(w),["2026-11-25","2026-11-26"])
        storm_blend.configure(w)
        self.assertEqual(len(storm_blend.HOURS),48)
        self.assertEqual(storm_blend.HOURS[0],int(dt.datetime(2026,11,25,7,tzinfo=UTC).timestamp()))
        self.assertEqual(ew.mode_kind("light_snow-20261127"),"light_snow")

if __name__=="__main__":unittest.main()
