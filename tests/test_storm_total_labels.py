import datetime as dt
import unittest
from scripts import storm_total_labels as L

D=lambda s:dt.date.fromisoformat(s)

def series(values,start="2010-01-01"):
    return {D(start)+dt.timedelta(days=i):v for i,v in enumerate(values)}

class LabelTests(unittest.TestCase):
    def test_event_needs_two_dry_days_each_side(self):
        P=series([0,0,5,8,0,0])
        self.assertEqual(list(L.find_events(P)),[(D("2010-01-03"),D("2010-01-04"),0.0)])
        self.assertEqual(list(L.find_events(series([0,0.5,5,0,0]))),[])   # wet-ish day before
        self.assertEqual(list(L.find_events(series([0,5,0,0]))),[])       # only one dry day before

    def test_long_runs_and_gaps_rejected(self):
        self.assertEqual(list(L.find_events(series([0,0,3,3,3,3,0,0]))),[])
        P=series([0,0,5,0,0]);del P[D("2010-01-05")]
        self.assertEqual(list(L.find_events(P)),[])

    def test_label_sums_reports_s_minus_1_to_e_plus_1_with_trace(self):
        obs={("ST","2010-01-02"):("measured",0.0),("ST","2010-01-03"):("measured",2.0),
             ("ST","2010-01-04"):("trace",0.0),("ST","2010-01-05"):("measured",3.0)}
        self.assertEqual(L.label(obs,"ST",D("2010-01-03"),D("2010-01-04")),(5.0,1))
        del obs[("ST","2010-01-02")]
        self.assertIsNone(L.label(obs,"ST",D("2010-01-03"),D("2010-01-04")))

    def test_legacy_mapping_offsets(self):
        obs={("ST","2010-01-03"):("measured",1.0),("ST","2010-01-04"):("measured",4.0)}
        # UTC day 01-03: utc_end_date uses report 01-04, utc_end_previous_date uses 01-03.
        self.assertEqual(L.mapping_label(obs,"ST",D("2010-01-03"),D("2010-01-03"),1),4.0)
        self.assertEqual(L.mapping_label(obs,"ST",D("2010-01-03"),D("2010-01-03"),0),1.0)

    def test_split_requires_named_seasons(self):
        self.assertEqual(L.split(D("2012-12-01")),"train")
        self.assertEqual(L.split(D("2016-02-01")),"validation")
        self.assertEqual(L.split(D("2017-02-01")),"test")
        self.assertIsNone(L.split(D("2019-12-01")))

if __name__=="__main__":unittest.main()
