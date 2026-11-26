import datetime as dt
import unittest
from scripts import storm_total_benchmark as b

class BenchmarkTests(unittest.TestCase):
    def test_same_issue_only(self):
        init=dt.date(2017,1,1)
        key=("utc_end_date",init,"station")
        other=("utc_end_date",init+dt.timedelta(days=1),"station")
        a={key:(init+dt.timedelta(days=1),{"actual_in":2,"rf_in":1})}
        d={key:(init+dt.timedelta(days=2),{"actual_in":3,"rf_in":2}),other:(init+dt.timedelta(days=3),{"actual_in":9,"rf_in":9})}
        rows=b.join(a,d)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["actual_in"],5)
        self.assertEqual(b.metrics(rows,"rf_in")["snow_case_mae"],2)
    def test_bad_dates(self):
        d=dt.date(2017,1,1);k=("utc_end_date",d,"s")
        with self.assertRaises(ValueError):b.join({k:(d,{"actual_in":1})},{k:(d,{"actual_in":2})})
if __name__=="__main__":unittest.main()
