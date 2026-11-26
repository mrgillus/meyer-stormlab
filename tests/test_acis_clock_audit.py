import unittest
from scripts import acis_clock_audit as audit

class AuditTest(unittest.TestCase):
    def test_candidate_only(self):
        raw={"meta":{"sids":["USW00013994 6"]},"data":[
            ["2000-01-01",["1.0",7,""]],["2000-01-02",["0.0",24,""]],
            ["2000-01-03",["0.0",0,""]],["2000-01-04",["1.0",7,"A"]],
            ["2000-01-05",["2.0",7,""]],["2000-01-06",["T",7,""]]]}
        expected={f"2000-01-{i:02d}":v for i,v in enumerate([(25,"","0"),(0,"","0"),(0,"","0"),(25,"","0"),(25,"","0"),(0,"T","0")],1)}
        report,rows=audit.compare("USW00013994",expected,raw)
        self.assertEqual(report["counts"]["clock_candidate"],2)
        self.assertEqual(report["counts"]["clock_unknown"],1)
        self.assertEqual(report["counts"]["flagged"],1)
        self.assertEqual(report["counts"]["amount_mismatch"],1)
        self.assertEqual(report["counts"]["trace_excluded"],1)
        self.assertTrue(all(not r["observation_interval_verified"] for r in rows))
    def test_identity(self):
        with self.assertRaises(ValueError):audit.compare("USW00013994",{},{"meta":{"sids":["other"]},"data":[]})
    def test_duplicate(self):
        raw={"meta":{"sids":["USW00013994 6"]},"data":[["2000-01-01",[0,7,""]],["2000-01-01",[0,8,""]]]}
        r,rows=audit.compare("USW00013994",{"2000-01-01":(0,"","0")},raw)
        self.assertEqual(r["counts"]["missing_or_duplicate"],1)
        self.assertEqual(rows,[])
if __name__=="__main__":unittest.main()
