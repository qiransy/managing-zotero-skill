from dataclasses import asdict
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from zotero_client import (
    ZoteroAuthorizationError,
    ZoteroClient,
    ZoteroLibraryLockedError,
    ZoteroVersionConflict,
)
from zotero_local import main
from zotero_models import CandidateItem, canonical_json, plan_digest
from zotero_workflow import ApprovalProof, build_collection_plan, build_item_plan, execute_plan

from fake_zotero_server import FakeZoteroServer


class ZoteroIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="zotero-integration-")
        self.temp = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def client(server):
        return ZoteroClient(base_url=server.api_url, test_mode=True)

    def audit_text(self):
        audit = self.temp / "audit" / "managing-zotero-audit.jsonl"
        return audit.read_text(encoding="utf-8") if audit.exists() else ""

    def run_cli(self, server, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(
            arguments,
            client_factory=lambda: self.client(server),
            stdout=stdout,
            stderr=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_preview_approve_write_readback_audit(self):
        with FakeZoteroServer(test_mode=True) as server:
            with self.assertRaises(ValueError):
                ZoteroClient(base_url=server.api_url)
            plan = build_collection_plan("synthetic-system", server.library_version)
            result = execute_plan(
                self.client(server),
                plan,
                ApprovalProof(plan_digest(plan), True),
                audit_dir=self.temp / "audit",
            )

            self.assertTrue(result.verified, result.failed)
            self.assertEqual(server.authorization_count, 1)
            self.assertEqual(server.write_count, 1)
            self.assertEqual(server.delete_count, 0)
            self.assertFalse(server.contains_issued_key(self.audit_text()))
            self.assertEqual(
                [record.path.split("?", 1)[0] for record in server.records],
                [
                    "/api/users/0/collections",
                    "/api/",
                    "/api/local/authorize",
                    "/api/users/0/collections",
                    "/api/users/0/collections/COL00001",
                ],
            )
            self.assertEqual(server.records[2].body, {"appName": "Codex managing-zotero"})
            self.assertEqual(server.records[3].body, [{"name": "synthetic-system"}])
            self.assertEqual(server.records[3].headers["If-Unmodified-Since-Version"], "1")
            self.assertEqual(server.records[3].headers["Zotero-Api-Key"], "[REDACTED]")
            self.assertEqual(server.records[0].failure_mode, "")
            self.assertEqual(server.records[-1].object_versions, {"COL00001": 2})

    def test_local_api_disabled_returns_403_without_authorization_or_write(self):
        with FakeZoteroServer(test_mode=True, failure_mode="local_api_disabled") as server:
            status = self.client(server).probe()
            self.assertFalse(status.connected)
            self.assertIn("403", status.reason)
            self.assertEqual(server.authorization_count, 0)
            self.assertEqual(server.write_count, 0)

    def test_unsupported_authorization_latches_read_only_for_405_and_501(self):
        for mode in ("authorize_405", "authorize_501"):
            with self.subTest(mode=mode):
                with FakeZoteroServer(test_mode=True, failure_mode=mode) as server:
                    client = self.client(server)
                    with self.assertRaises(ZoteroAuthorizationError):
                        client.authorize_once()
                    status = client.probe()
                    self.assertTrue(status.connected)
                    self.assertFalse(status.write_candidate)
                    self.assertIn("read-only", status.reason)
                    self.assertEqual(server.write_count, 0)

    def test_authorization_denial_stops_without_write(self):
        with FakeZoteroServer(test_mode=True, failure_mode="authorization_denied") as server:
            with self.assertRaises(ZoteroAuthorizationError):
                self.client(server).authorize_once()
            self.assertEqual(server.authorization_count, 0)
            self.assertEqual(server.write_count, 0)

    def test_locked_library_is_not_retried_and_exception_is_secret_free(self):
        with FakeZoteroServer(test_mode=True, failure_mode="library_locked") as server:
            plan = build_collection_plan("synthetic-system", server.library_version)
            with self.assertRaises(ZoteroLibraryLockedError) as caught:
                execute_plan(
                    self.client(server),
                    plan,
                    ApprovalProof(plan_digest(plan), True),
                    audit_dir=self.temp / "audit",
                )
            self.assertEqual(server.write_count, 1)
            self.assertFalse(server.contains_issued_key(str(caught.exception)))

    def test_412_stale_write_is_not_blindly_retried(self):
        with FakeZoteroServer(test_mode=True, failure_mode="stale_write") as server:
            plan = build_collection_plan("synthetic-system", server.library_version)
            with self.assertRaises(ZoteroVersionConflict):
                execute_plan(
                    self.client(server),
                    plan,
                    ApprovalProof(plan_digest(plan), True),
                    audit_dir=self.temp / "audit",
                )
            self.assertEqual(server.authorization_count, 1)
            self.assertEqual(server.write_count, 1)

    def test_mixed_item_result_reports_exact_actual_state(self):
        with FakeZoteroServer(test_mode=True, failure_mode="mixed_items") as server:
            server.seed_collection("COLLECT01", "Synthetic", version=3)
            candidates = (
                CandidateItem(title="Synthetic paper one", doi="10.0000/synthetic-1"),
                CandidateItem(title="Synthetic paper two", doi="10.0000/synthetic-2"),
            )
            plan = build_item_plan(candidates, server.collections["COLLECT01"], ())
            result = execute_plan(
                self.client(server),
                plan,
                ApprovalProof(plan_digest(plan), True),
                audit_dir=self.temp / "audit",
            )
            self.assertEqual(result.successful_keys, ("ITEM00001",))
            self.assertEqual(result.unchanged_keys, ())
            self.assertEqual(result.failed["1"], "injected item failure")
            self.assertIn("1.object", result.failed)
            self.assertFalse(result.verified)
            self.assertEqual(server.write_count, 1)

    def test_server_id_change_after_preview_stops_before_write(self):
        with FakeZoteroServer(test_mode=True) as server:
            plan = build_collection_plan("synthetic-system", server.library_version)
            server.set_failure("server_id_changed_on_authorize")
            with self.assertRaises(ZoteroAuthorizationError):
                execute_plan(
                    self.client(server),
                    plan,
                    ApprovalProof(plan_digest(plan), True),
                    audit_dir=self.temp / "audit",
                )
            self.assertEqual(server.authorization_count, 1)
            self.assertEqual(server.write_count, 0)

    def test_duplicate_doi_reuses_item_and_preserves_personal_note(self):
        with FakeZoteroServer(test_mode=True) as server:
            server.seed_collection("COLLECT01", "Synthetic", version=2)
            server.seed_item(
                "ITEM00001",
                title="Existing synthetic paper",
                doi="10.0000/reused",
                collections=("OTHER0001",),
                version=3,
                personal_note="User-owned note",
            )
            client = self.client(server)
            collection_response, _ = client.get_json("users/0/collections/COLLECT01")
            existing, _ = client.get_json("users/0/items", {"doi": "10.0000/reused"})
            plan = build_item_plan(
                (CandidateItem(title="Existing synthetic paper", doi="10.0000/reused"),),
                collection_response["data"],
                existing,
            )
            result = execute_plan(
                client,
                plan,
                ApprovalProof(plan_digest(plan), True),
                audit_dir=self.temp / "audit",
            )
            self.assertTrue(result.verified, result.failed)
            self.assertEqual(tuple(server.items), ("ITEM00001",))
            self.assertEqual(server.items["ITEM00001"]["collections"], ["OTHER0001", "COLLECT01"])
            self.assertEqual(server.items["ITEM00001"]["tags"], [{"tag": "personal"}])
            children, _ = client.get_json("users/0/items/ITEM00001/children")
            self.assertEqual(children[0]["note"], "User-owned note")

    def test_linked_pdf_missing_or_outside_approved_root_is_rejected_before_write(self):
        with FakeZoteroServer(test_mode=True) as server:
            server.seed_collection("COLLECT01", "Synthetic")
            allowed = self.temp / "approved"
            allowed.mkdir()
            outside = self.temp / "outside.pdf"
            outside.write_bytes(b"synthetic")
            for label, linked_pdf in (
                ("missing", allowed / "missing.pdf"),
                ("outside", outside),
            ):
                with self.subTest(label=label):
                    candidates = self.temp / f"{label}.json"
                    candidates.write_text(
                        json.dumps([{"title": "Synthetic paper", "linked_pdf": str(linked_pdf)}]),
                        encoding="utf-8",
                    )
                    code, _, _ = self.run_cli(server, [
                        "preview-items",
                        "--input", str(candidates),
                        "--collection-key", "COLLECT01",
                        "--profile", "generic",
                        "--allowed-root", str(allowed),
                        "--output", str(self.temp / f"{label}-plan.json"),
                    ])
                    self.assertEqual(code, 2)
            self.assertEqual(server.authorization_count, 0)
            self.assertEqual(server.write_count, 0)

    def test_key_is_absent_from_cli_streams_audit_and_persistable_request_records(self):
        with FakeZoteroServer(test_mode=True, failure_mode="library_locked") as server:
            plan = build_collection_plan("synthetic-system", server.library_version)
            plan_path = self.temp / "plan.json"
            plan_path.write_text(canonical_json(plan) + "\n", encoding="utf-8")
            code, stdout, stderr = self.run_cli(server, [
                "apply",
                "--plan", str(plan_path),
                "--approval-digest", plan_digest(plan),
                "--confirm-user-approved",
                "--audit-dir", str(self.temp / "audit"),
            ])
            self.assertEqual(code, 3)
            durable_records = json.dumps([asdict(record) for record in server.records], ensure_ascii=False)
            combined = "\n".join((stdout, stderr, self.audit_text(), durable_records))
            self.assertFalse(server.contains_issued_key(combined))


if __name__ == "__main__":
    unittest.main()
