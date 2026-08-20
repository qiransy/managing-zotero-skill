import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotero_local import main


class _FakeClient:
    def __init__(self):
        self.authorization_calls = 0
        self.write_calls = 0

    def probe(self):
        return type("Capability", (), {
            "connected": True, "api_version": "3", "schema_version": "44",
            "server_id": "SERVER-SECRET", "write_candidate": True, "reason": "",
        })()

    def get_json(self, path, query=None):
        if path == "users/0/collections":
            return [], type("Response", (), {"headers": {"Last-Modified-Version": "7"}})()
        if path == "users/0/collections/COLLECT01":
            return {"key": "COLLECT01", "name": "Research"}, None
        return [], None

    def get_versions(self, keys):
        return {key: None for key in keys}

    def authorize_once(self):
        self.authorization_calls += 1
        return object()

    def post_json(self, path, payload, authorization, expected_version=None):
        self.write_calls += 1
        return {"successful": {"0": "COLLECT01"}, "unchanged": {}, "failed": {}}, None


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="cli-")
        self.plan_path = str(Path(self.tempdir.name) / "plan.json")
        self.fake_client = _FakeClient()

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cli(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(arguments, client_factory=lambda: self.fake_client, stdout=stdout, stderr=stderr)
        return type("Result", (), {"code": code, "stdout": stdout.getvalue(), "stderr": stderr.getvalue()})()

    def test_help_has_no_delete_or_raw_authorize_command(self):
        result = self.run_cli(["--help"])
        self.assertEqual(result.code, 0)
        self.assertNotIn("delete", result.stdout.lower())
        self.assertNotIn("authorize", result.stdout.lower())

    def test_apply_requires_digest_and_confirmation_flag(self):
        Path(self.plan_path).write_text("{}", encoding="utf-8")
        result = self.run_cli(["apply", "--plan", self.plan_path])
        self.assertNotEqual(result.code, 0)
        self.assertIn("approval-digest", result.stderr)
        self.assertEqual(self.fake_client.write_calls, 0)

    def test_status_never_requests_authorization(self):
        result = self.run_cli(["status"])
        self.assertEqual(result.code, 0)
        self.assertEqual(self.fake_client.authorization_calls, 0)
        self.assertNotIn("SERVER-SECRET", result.stdout)

    def test_preview_collection_writes_plan_without_authorization(self):
        result = self.run_cli(["preview-collection", "--name", "Research", "--output", self.plan_path])
        self.assertEqual(result.code, 0)
        self.assertTrue(Path(self.plan_path).is_file())
        self.assertEqual(self.fake_client.authorization_calls, 0)
        self.assertEqual(json.loads(result.stdout)["operation"], "create_collection")

    def test_apply_executes_only_the_exact_confirmed_preview(self):
        self.assertEqual(self.run_cli(["preview-collection", "--name", "Research", "--output", self.plan_path]).code, 0)
        plan = json.loads(Path(self.plan_path).read_text(encoding="utf-8"))
        digest = __import__("hashlib").sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        audit_dir = str(Path(self.tempdir.name) / "audit")
        result = self.run_cli([
            "apply", "--plan", self.plan_path, "--approval-digest", digest,
            "--confirm-user-approved", "--audit-dir", audit_dir,
        ])
        self.assertEqual(result.code, 0)
        self.assertEqual(self.fake_client.authorization_calls, 1)
        self.assertEqual(self.fake_client.write_calls, 1)


if __name__ == "__main__":
    unittest.main()
