import unittest
from scripts.shadow_archive_audit import classify

class ArchiveAuditTests(unittest.TestCase):
    def setUp(self):
        self.window = dict(status="research_only",
            valid_start_utc="2026-11-27T00:00:00+00:00",
            valid_end_utc="2026-11-28T00:00:00+00:00",
            mappings=[{"model_artifact":{"sha256":"a"*64}}])
    def check(self, time, expected):
        self.assertEqual(classify({"generated_at":time}, self.window), expected)
    def test_before(self):
        self.check("2026-11-26T23:59:59Z", "pre_window_candidate")
    def test_start_boundary(self):
        self.check("2026-11-27T00:00:00Z", "during_window")
    def test_end_boundary(self):
        self.check("2026-11-28T00:00:00Z", "after_window")
    def test_missing_provenance(self):
        self.window["mappings"] = []
        self.check("2026-11-26T23:59:59Z", "missing_artifact_provenance")
    def test_naive_time(self):
        self.check("2026-11-26T23:59:59", "invalid_timestamp")
    def test_incomplete(self):
        self.window["status"] = "incomplete"
        self.check("2026-11-26T23:59:59Z", "incomplete")
