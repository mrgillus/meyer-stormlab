"""Regression tests for post-training shadow inference deduplication."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import regional_live_v2 as live

class StaleTests(unittest.TestCase):
    def test_current_complete_only(self):
        now=dt.datetime(2026,11,27,12,tzinfo=dt.timezone.utc)
        reports={lead:"current" for lead in (24,48,72,168)}
        rows=[dict(initialization_utc="20261127T00Z",lead_hours=lead,
                   status="research_only",training_generated_at="current") for lead in reports]
        with tempfile.TemporaryDirectory() as d, patch.object(live,"training_reports",return_value=reports):
            p=Path(d)/"status.json"
            def check(windows):
                p.write_text(json.dumps({"windows":windows}))
                return live.artifacts_already_consumed(p,now)
            self.assertTrue(check(rows))
            self.assertFalse(check(rows[:-1]))
            self.assertFalse(check([dict(r,initialization_utc="20261126T00Z") for r in rows]))
            self.assertFalse(check([dict(rows[0],status="incomplete"),*rows[1:]]))
            self.assertFalse(check([dict(rows[0],training_generated_at="old"),*rows[1:]]))
            self.assertFalse(check([]))
if __name__=="__main__":unittest.main()
