import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import gefs_stations as g

def manifest(n=2):
    m=dict(schema=1,archive="t",members=list(g.MEMBERS),lead_hours=list(g.LEADS),fields=list(g.FIELDS),decimals=2,
           points=[dict(label=f"S{i}",lat=38.0+i,lon=-90.0) for i in range(n)])
    m["fingerprint"]=hashlib.sha256(json.dumps(m,sort_keys=True).encode()).hexdigest()
    return m

def shard(m,rows,complete):
    return dict(schema=1,date="20100101",fingerprint=m["fingerprint"],attempts=1,rows=rows,errors=[],core_complete=complete)

def row(member,lead,field,values):
    return dict(member=member,lead_hours=lead,field=field,values=values)

class StationArchiveTests(unittest.TestCase):
    def test_manifest_fingerprint_is_enforced(self):
        with tempfile.TemporaryDirectory() as t:
            old=g.OUT;g.OUT=Path(t)
            try:
                m=manifest();(g.OUT/"manifest.json").write_text(json.dumps(m))
                self.assertEqual(g.manifest()["fingerprint"],m["fingerprint"])
                m["points"].append(dict(label="X",lat=1,lon=1));(g.OUT/"manifest.json").write_text(json.dumps(m))
                with self.assertRaises(ValueError):g.manifest()
            finally:g.OUT=old

    def test_validate_complete_and_rejects_bad_values(self):
        m=manifest()
        full=[row(mem,l,f,[270.0,271.0] if f!="precip24" else [1.0,0.0]) for mem in g.MEMBERS for l in g.LEADS for f in g.FIELDS]
        self.assertTrue(g.validate(shard(m,full,True),m)["core_complete"])
        with self.assertRaises(ValueError):g.validate(shard(m,full[:-1],True),m)          # flag lies about coverage
        bad=[dict(r,values=[15.0,16.0]) if r["field"]=="t2m" else r for r in full]
        with self.assertRaises(ValueError):g.validate(shard(m,bad,True),m)               # Celsius, not Kelvin
        neg=[dict(r,values=[-1.0,0.0]) if r["field"]=="precip24" else r for r in full]
        with self.assertRaises(ValueError):g.validate(shard(m,neg,True),m)
        with self.assertRaises(ValueError):g.validate(shard(m,full+[full[0]],True),m)     # duplicate

if __name__=="__main__":unittest.main()
