import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotero_audit import AuditEvent, append_audit_event, redact


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix="audit-")
        self.audit_dir = Path(self.tempdir.name).resolve()
        self.fsync_patch = patch("zotero_audit.os.fsync")
        self.fsync = self.fsync_patch.start()

    def tearDown(self):
        self.fsync_patch.stop()
        self.tempdir.cleanup()

    def test_audit_appends_without_rewriting_prior_line(self):
        first = AuditEvent(plan_digest="a" * 64, operation="create_collection", outcome="success")
        second = AuditEvent(plan_digest="b" * 64, operation="upsert_items", outcome="partial")
        path = append_audit_event(self.audit_dir, first)
        original_first_line = path.read_text(encoding="utf-8").splitlines()[0]
        append_audit_event(self.audit_dir, second)
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], original_first_line)
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["outcome"], "partial")
        self.assertEqual(self.fsync.call_count, 2)

    def test_secret_fields_and_note_bodies_are_redacted_but_item_keys_remain(self):
        event = AuditEvent(
            plan_digest="c" * 64,
            operation="upsert_items",
            outcome="success",
            details={
                "Zotero-API-Key": "header-secret",
                "key": "local-secret",
                "itemKey": "ITEM0001",
                "nested": {"authorization": "Bearer private", "note": "personal note body"},
                "headers": {"X-Zotero-API-Key": "another-secret"},
            },
        )
        path = append_audit_event(self.audit_dir, event)
        text = path.read_text(encoding="utf-8")
        for secret in ("header-secret", "local-secret", "Bearer private", "personal note body", "another-secret"):
            self.assertNotIn(secret, text)
        self.assertIn("ITEM0001", text)

    def test_relative_audit_directory_is_rejected(self):
        event = AuditEvent(plan_digest="d" * 64, operation="upsert_items", outcome="success")
        with self.assertRaises(ValueError):
            append_audit_event("relative-audit", event)

    def test_redact_drops_raw_request_and_response_payloads(self):
        result = redact({"request_payload": {"title": "paper"}, "response": {"data": "private"}, "reason": "invalid item"})
        self.assertEqual(result["request_payload"], "[redacted]")
        self.assertEqual(result["response"], "[redacted]")
        self.assertEqual(result["reason"], "invalid item")


if __name__ == "__main__":
    unittest.main()
