import unittest
from scripts import storm_total_rescore as R

class RescoreTests(unittest.TestCase):
    def test_metrics_by_class_and_ratio(self):
        rows=[dict(observed_in=5.0,p=2.0,start_utc="a",end_utc="b"),dict(observed_in=0.0,p=0.5,start_utc="c",end_utc="d")]
        m=R.metrics(rows,"p","observed_in")
        self.assertEqual(m["observed_ge_4in"]["bias_in"],-3.0)
        self.assertEqual(m["observed_ge_4in"]["ratio_pred_to_truth"],0.4)
        self.assertIsNone(m["observed_lt_1in"]["ratio_pred_to_truth"])
        self.assertEqual(m["all"]["n"],2)

    def test_legacy_offsets_match_label_builder(self):
        self.assertEqual(R.MAPPINGS,{"utc_end_date":1,"utc_end_previous_date":0})

if __name__=="__main__":unittest.main()
