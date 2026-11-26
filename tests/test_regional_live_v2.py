import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
joblib=None
try:
    import joblib
    import regional_live_v2 as v2
except ImportError:  # Inference dependencies (eccodes etc.) not installed.
    v2=None

def points(n_local=3):
    syn=[dict(label=f"synoptic_{i}",lat=30+i,lon=-100+i,kind="synoptic") for i in range(10)]
    return syn+[dict(label=f"station_{i}",lat=38+i*.1,lon=-90,kind="station") for i in range(n_local)]

def data(pts):
    # Distinct, identifiable value per field/member/point.
    f,m,p=np.meshgrid(np.arange(6),np.arange(5),np.arange(len(pts)),indexing="ij")
    return 1000.*f+10.*p+m

@unittest.skipIf(v2 is None,"inference dependencies unavailable")
class FeatureNameTests(unittest.TestCase):
    def test_names_match_vector_layout(self):
        pts=points();d=data(pts)
        names=[n for n,_ in v2.feature_names(pts)]
        x=v2.live.vector(d,pts,11,dt.date(2026,11,26))
        self.assertEqual(len(names),len(x))
        mean=d.mean(axis=1);sd=d.std(axis=1)
        for k,name in enumerate(names):
            if name.startswith("synoptic_"):
                stat,rest=name[len("synoptic_"):].split("_",1)
                field,where=rest.split("@")
                fi=v2.learn.FIELDS.index(field);pi=int(where.split("(")[0].split("_")[1])
                self.assertAlmostEqual(x[k],(mean if stat=="mean" else sd)[fi,pi])
            elif name.startswith("local_mean_"):
                self.assertAlmostEqual(x[k],mean[v2.learn.FIELDS.index(name[11:]),11])
        self.assertEqual(names[103],"synoptic_sd_z500@synoptic_5(35,-95)")

@unittest.skipIf(v2 is None,"inference dependencies unavailable")
class AnnotateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.dir=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.old_root=v2.ROOT;v2.ROOT=self.dir
        self.addCleanup(setattr,v2,"ROOT",self.old_root)
        self.pts=points();self.d=data(self.pts);self.target=dt.date(2026,11,26)
        X=np.stack([v2.live.vector(self.d,self.pts,i,self.target) for i in range(10,len(self.pts))])
        lo=X.min(axis=0)-1;hi=X.max(axis=0)+1
        hi[103]=X[0,103]-5   # Force one shared synoptic feature above range.
        joblib.dump(dict(training_min=lo,training_max=hi,feature_version=1,train_seasons=[2000]),self.dir/"research_24_utc_end_date.joblib")
        self.hi=hi

    def window(self,flags):
        rows=[dict(label=p["label"],outside_training_features=flags) for p in self.pts[10:]]
        return dict(lead_hours=24,mappings=[dict(label_mapping="utc_end_date",reported_date_proxy=self.target.isoformat(),points=rows)])

    def test_flag_detail_and_provenance(self):
        w=v2.annotate(self.window([103]),self.d,self.pts,self.dir)
        m=w["mappings"][0]
        detail=m["points"][0]["outside_training_detail"][0]
        self.assertEqual(detail["name"],"synoptic_sd_z500@synoptic_5(35,-95)")
        self.assertEqual(detail["direction"],"above")
        self.assertGreater(detail["value"],detail["training_max"])
        s=m["out_of_range_summary"]
        self.assertEqual(len(s),1);self.assertTrue(s[0]["shared_synoptic_feature"]);self.assertEqual(s[0]["points_flagged"],3)
        self.assertEqual(len(m["model_artifact"]["sha256"]),64)
        self.assertEqual(m["model_artifact"]["path"],"research_24_utc_end_date.joblib")

    def test_disagreeing_flags_rejected(self):
        with self.assertRaises(ValueError):
            v2.annotate(self.window([]),self.d,self.pts,self.dir)

@unittest.skipIf(v2 is None,"inference dependencies unavailable")
class StalenessTests(unittest.TestCase):
    def test_consumed_only_when_all_leads_current(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/"status.json"
            current=v2.training_reports()
            self.assertFalse(v2.artifacts_already_consumed(path))
            windows=[dict(lead_hours=l,training_generated_at=g) for l,g in current.items()]
            path.write_text(json.dumps(dict(windows=windows)))
            self.assertEqual(v2.artifacts_already_consumed(path),bool(windows))
            windows[0]["training_generated_at"]="2000-01-01T00:00:00+00:00"
            path.write_text(json.dumps(dict(windows=windows)))
            self.assertFalse(v2.artifacts_already_consumed(path))

if __name__=="__main__":unittest.main()
