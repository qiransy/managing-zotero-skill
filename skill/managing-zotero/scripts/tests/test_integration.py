from dataclasses import asdict
from hashlib import sha256
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
            plan = build_collection_plan("synthetic-system", server.library_version, server_id=server.server_id)
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
                    "/api/",
                    "/api/users/0/collections",
                    "/api/",
                    "/api/local/authorize",
                    "/api/users/0/collections",
                    "/api/users/0/collections/COL00001",
                ],
            )
            self.assertEqual(server.records[3].body, {"appName": "Codex managing-zotero"})
            self.assertEqual(server.records[4].body, [{"name": "synthetic-system"}])
            self.assertEqual(server.records[4].headers["If-Unmodified-Since-Version"], "1")
            self.assertEqual(server.records[4].headers["Zotero-Api-Key"], "[REDACTED]")
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

    def test_fake_matches_real_zotero_keyed_new_item_failure(self):
        with FakeZoteroServer(test_mode=True) as server:
            client = self.client(server)
            authorization = client.authorize_once()
            response, _ = client.post_json(
                "users/0/items",
                [{
                    "itemType": "journalArticle",
                    "key": "PRESET01",
                    "version": 0,
                    "title": "Synthetic keyed item",
                    "creators": [],
                    "collections": [],
                }],
                authorization,
            )
            self.assertEqual(response["successful"], {})
            self.assertIn("primaryData", response["failed"]["0"]["message"])
            self.assertNotIn("PRESET01", server.items)

    def test_fake_assigns_unique_keys_to_keyless_children_across_parents(self):
        with FakeZoteroServer(test_mode=True) as server:
            server.seed_item("PARENT01", title="Parent one", doi="10.0000/one")
            server.seed_item("PARENT02", title="Parent two", doi="10.0000/two")
            client = self.client(server)
            response, _ = client.post_json(
                "users/0/items",
                [
                    {"itemType": "note", "parentItem": "PARENT01", "note": "one"},
                    {"itemType": "note", "parentItem": "PARENT02", "note": "two"},
                ],
                client.authorize_once(),
            )
            keys = [response["successful"][str(index)]["key"] for index in range(2)]
            self.assertEqual(len(set(keys)), 2)

    def test_locked_library_is_not_retried_and_exception_is_secret_free(self):
        with FakeZoteroServer(test_mode=True, failure_mode="library_locked") as server:
            plan = build_collection_plan("synthetic-system", server.library_version, server_id=server.server_id)
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
            plan = build_collection_plan("synthetic-system", server.library_version, server_id=server.server_id)
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
            plan = build_item_plan(candidates, server.collections["COLLECT01"], (), server_id=server.server_id)
            result = execute_plan(
                self.client(server),
                plan,
                ApprovalProof(plan_digest(plan), True),
                audit_dir=self.temp / "audit",
            )
            self.assertEqual(result.successful_keys, ("ITEM00001",))
            self.assertEqual(result.resolved_keys, ("ITEM00001", ""))
            self.assertEqual(result.unchanged_keys, ())
            self.assertEqual(result.failed["1"], "injected item failure")
            self.assertNotIn("1.object", result.failed)
            self.assertFalse(result.verified)
            self.assertEqual(server.write_count, 1)

    def test_server_id_change_after_preview_stops_before_write(self):
        with FakeZoteroServer(test_mode=True) as server:
            plan = build_collection_plan("synthetic-system", server.library_version, server_id=server.server_id)
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
                server_id=server.server_id,
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
            plan = build_collection_plan("synthetic-system", server.library_version, server_id=server.server_id)
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

    @staticmethod
    def server_fingerprint(server):
        return sha256(server.server_id.encode("utf-8")).hexdigest()

    def test_apply_rejects_malicious_loaded_plan_payloads_before_authorization(self):
        malicious_payloads = (
            {"itemType": "journalArticle", "title": "Synthetic", "deleted": True, "collections": ["COLLECT01"]},
            {"itemType": "journalArticle", "title": "Synthetic", "unknownMutation": "x", "collections": ["COLLECT01"]},
            {"itemType": "note", "parentItem": "ITEM00001", "note": "overwrite personal note", "key": "NOTE00001", "version": 1},
            {"itemType": "journalArticle", "key": "ITEM00001", "collections": ["COLLECT01"], "tags": []},
            {"itemType": "attachment", "parentItem": "ITEM00001", "linkMode": "linked_file", "path": "C:\\cache\\paper.pdf"},
        )
        for index, payload in enumerate(malicious_payloads):
            with self.subTest(index=index), FakeZoteroServer(test_mode=True) as server:
                server.seed_collection("COLLECT01", "Synthetic")
                server.seed_item("ITEM00001", title="Existing", doi="10.0000/existing")
                plan = {
                    "operation": "upsert_items",
                    "collection_key": "COLLECT01",
                    "collection_name": "Synthetic",
                    "actions": [{"kind": "upsert_items", "payload": [payload], "item_key": ""}],
                    "expected_versions": {"COLLECT01": 1},
                    "library_version": None,
                    "server_fingerprint": self.server_fingerprint(server),
                    "duplicate_checks": [],
                    "allowed_roots": [],
                }
                plan_path = self.temp / f"malicious-{index}.json"
                plan_path.write_text(json.dumps(plan), encoding="utf-8")
                digest = sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                code, _, _ = self.run_cli(server, [
                    "apply", "--plan", str(plan_path), "--approval-digest", digest,
                    "--confirm-user-approved", "--audit-dir", str(self.temp / "audit"),
                ])
                self.assertEqual(code, 2)
                self.assertEqual(server.authorization_count, 0)
                self.assertEqual(server.write_count, 0)

    def test_marked_note_cannot_target_an_existing_personal_note(self):
        with FakeZoteroServer(test_mode=True) as server:
            server.seed_collection("COLLECT01", "Synthetic", version=1)
            server.seed_item(
                "ITEM00001",
                title="Existing",
                doi="10.0000/existing",
                version=1,
                personal_note="User-owned personal note",
            )
            note_key = "NOTE-ITEM00001"
            plan = {
                "operation": "upsert_items",
                "collection_key": "COLLECT01",
                "collection_name": "Synthetic",
                "actions": [{
                    "kind": "upsert_items",
                    "payload": [{
                        "itemType": "note",
                        "key": note_key,
                        "version": 1,
                        "parentItem": "ITEM00001",
                        "note": '<div data-codex-note="evidence-bounded-v1">replace</div>',
                    }],
                    "item_key": "",
                }],
                "expected_versions": {"COLLECT01": 1, note_key: 1},
                "library_version": None,
                "server_fingerprint": self.server_fingerprint(server),
                "duplicate_checks": [],
                "allowed_roots": [],
            }
            plan_path = self.temp / "marked-note-overwrite.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            digest = sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            code, _, _ = self.run_cli(server, [
                "apply", "--plan", str(plan_path), "--approval-digest", digest,
                "--confirm-user-approved", "--audit-dir", str(self.temp / "audit"),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(server.authorization_count, 0)
            self.assertEqual(server.write_count, 0)
            self.assertEqual(server.children["ITEM00001"][0]["note"], "User-owned personal note")

    def test_preview_digest_is_bound_to_server_identity(self):
        plan_path = self.temp / "identity-plan.json"
        with FakeZoteroServer(test_mode=True) as preview_server:
            code, output, _ = self.run_cli(preview_server, [
                "preview-collection", "--name", "Synthetic", "--output", str(plan_path),
            ])
            self.assertEqual(code, 0, output)
            digest = json.loads(output)["digest"]
        with FakeZoteroServer(test_mode=True) as apply_server:
            apply_server.server_id = "FAKE-SERVER-DIFFERENT"
            code, _, _ = self.run_cli(apply_server, [
                "apply", "--plan", str(plan_path), "--approval-digest", digest,
                "--confirm-user-approved", "--audit-dir", str(self.temp / "audit"),
            ])
            self.assertEqual(code, 5)
            self.assertEqual(apply_server.authorization_count, 0)
            self.assertEqual(apply_server.write_count, 0)

    def test_preview_items_dedupes_library_wide_blocks_probable_and_rechecks_before_apply(self):
        with FakeZoteroServer(test_mode=True) as server:
            server.seed_collection("COLLECT01", "Target", version=2)
            server.seed_item("ITEM00001", title="Existing DOI", doi="10.0000/reused", collections=("OTHER",), version=3)
            exact_input = self.temp / "exact.json"
            exact_input.write_text(json.dumps([{"title": "Candidate title", "doi": "10.0000/reused", "creators": [], "tags": []}]), encoding="utf-8")
            exact_plan = self.temp / "exact-plan.json"
            code, _, _ = self.run_cli(server, [
                "preview-items", "--input", str(exact_input), "--collection-key", "COLLECT01",
                "--profile", "generic", "--allowed-root", str(self.temp), "--output", str(exact_plan),
            ])
            self.assertEqual(code, 0)
            payload = json.loads(exact_plan.read_text(encoding="utf-8"))["actions"][0]["payload"][0]
            self.assertEqual(payload["key"], "ITEM00001")

            server.seed_item("ITEM00002", title="Probable paper", doi="", collections=("OTHER",), version=4)
            server.items["ITEM00002"]["creators"] = [{"lastName": "Smith"}]
            server.items["ITEM00002"]["date"] = "2025"
            probable_input = self.temp / "probable.json"
            probable_input.write_text(json.dumps([{"title": "Probable paper", "year": "2025", "creators": [{"lastName": "Smith"}], "tags": []}]), encoding="utf-8")
            code, _, _ = self.run_cli(server, [
                "preview-items", "--input", str(probable_input), "--collection-key", "COLLECT01",
                "--profile", "generic", "--allowed-root", str(self.temp), "--output", str(self.temp / "probable-plan.json"),
            ])
            self.assertEqual(code, 2)

            new_input = self.temp / "new.json"
            new_input.write_text(json.dumps([{"title": "Concurrent paper", "doi": "10.0000/concurrent", "creators": [], "tags": []}]), encoding="utf-8")
            new_plan = self.temp / "new-plan.json"
            code, output, _ = self.run_cli(server, [
                "preview-items", "--input", str(new_input), "--collection-key", "COLLECT01",
                "--profile", "generic", "--allowed-root", str(self.temp), "--output", str(new_plan),
            ])
            self.assertEqual(code, 0)
            server.seed_item("ITEM00003", title="Concurrent paper", doi="10.0000/concurrent", collections=("OTHER",), version=5)
            code, _, _ = self.run_cli(server, [
                "apply", "--plan", str(new_plan), "--approval-digest", json.loads(output)["digest"],
                "--confirm-user-approved", "--audit-dir", str(self.temp / "audit"),
            ])
            self.assertEqual(code, 5)
            self.assertEqual(server.authorization_count, 0)

    def test_production_preview_applies_codex_note_attachment_and_r7_evidence_state(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix="zotero-final-") as final_dir:
            final_root = Path(final_dir)
            pdf = final_root / "synthetic.pdf"
            pdf.write_bytes(b"%PDF-1.7")
            with FakeZoteroServer(test_mode=True) as server:
                server.seed_collection("COLLECT01", "Target", version=2)
                candidate_path = final_root / "candidate.json"
                candidate_path.write_text(json.dumps([{
                    "title": "Synthetic paper", "doi": "10.0000/children",
                    "evidence_level": "abstract_only", "linked_pdf": str(pdf),
                    "creators": [],
                    "tags": ["实验：CP-FTMW"], "note_fields": {"relevance": "摘要支持相关性"},
                }], ensure_ascii=False), encoding="utf-8")
                plan_path = final_root / "parent-plan.json"
                parent_result_path = final_root / "parent-result.json"
                code, output, error = self.run_cli(server, [
                    "preview-items", "--input", str(candidate_path), "--collection-key", "COLLECT01",
                    "--profile", "microwave-spectroscopy", "--allowed-root", str(final_root), "--output", str(plan_path),
                ])
                self.assertEqual(code, 0, error)
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                payloads = plan["actions"][0]["payload"]
                self.assertEqual([value["itemType"] for value in payloads], ["journalArticle"])
                self.assertNotIn("key", payloads[0])
                self.assertNotIn("version", payloads[0])
                self.assertNotIn("状态：待获取全文", [tag["tag"] for tag in payloads[0]["tags"]])
                code, _, error = self.run_cli(server, [
                    "apply", "--plan", str(plan_path), "--approval-digest", json.loads(output)["digest"],
                    "--confirm-user-approved", "--audit-dir", str(final_root / "audit"),
                    "--result-output", str(parent_result_path),
                ])
                self.assertEqual(code, 0, error)
                parent_result = json.loads(parent_result_path.read_text(encoding="utf-8"))
                parent = parent_result["resolved_keys"][0]
                child_plan_path = final_root / "child-plan.json"
                code, output, error = self.run_cli(server, [
                    "preview-children", "--input", str(candidate_path),
                    "--parent-plan", str(plan_path), "--parent-result", str(parent_result_path),
                    "--collection-key", "COLLECT01", "--profile", "microwave-spectroscopy",
                    "--allowed-root", str(final_root), "--output", str(child_plan_path),
                ])
                self.assertEqual(code, 0, error)
                child_payloads = json.loads(child_plan_path.read_text(encoding="utf-8"))["actions"][0]["payload"]
                self.assertEqual([value["itemType"] for value in child_payloads], ["note", "attachment"])
                self.assertTrue(all(value["parentItem"] == parent for value in child_payloads))
                self.assertTrue(all("key" not in value and "version" not in value for value in child_payloads))
                self.assertIn('data-codex-note="evidence-bounded-v1"', child_payloads[0]["note"])
                self.assertIn("基于摘要；全文已获取，尚未深读", child_payloads[0]["note"])
                self.assertIn("本地详细报告", child_payloads[0]["note"])
                self.assertNotIn("D 盘详细报告", child_payloads[0]["note"])
                code, _, error = self.run_cli(server, [
                    "apply", "--plan", str(child_plan_path), "--approval-digest", json.loads(output)["digest"],
                    "--confirm-user-approved", "--audit-dir", str(final_root / "audit"),
                ])
                self.assertEqual(code, 0, error)
                self.assertEqual([child["itemType"] for child in server.children[parent]], ["note", "attachment"])
                self.assertEqual(server.authorization_count, 2)
                self.assertEqual(server.write_count, 2)

    def test_first_item_failure_preserves_index_mapping_and_cli_emits_partial_json(self):
        with FakeZoteroServer(test_mode=True, failure_mode="first_item_failure") as server:
            server.seed_collection("COLLECT01", "Target", version=2)
            candidates = self.temp / "partial.json"
            candidates.write_text(json.dumps([
                {"title": "First", "doi": "10.0000/first", "creators": [], "tags": []},
                {"title": "Second", "doi": "10.0000/second", "creators": [], "tags": []},
            ]), encoding="utf-8")
            plan_path = self.temp / "partial-plan.json"
            code, output, error = self.run_cli(server, [
                "preview-items", "--input", str(candidates), "--collection-key", "COLLECT01",
                "--profile", "generic", "--allowed-root", str(self.temp), "--output", str(plan_path),
            ])
            self.assertEqual(code, 0, error)
            digest = json.loads(output)["digest"]
            code, output, error = self.run_cli(server, [
                "apply", "--plan", str(plan_path), "--approval-digest", digest,
                "--confirm-user-approved", "--audit-dir", str(self.temp / "audit"),
            ])
            self.assertEqual(code, 6)
            partial = json.loads(output)
            self.assertEqual(partial["successful_keys"], ["ITEM00001"])
            self.assertEqual(partial["resolved_keys"], ["", "ITEM00001"])
            self.assertEqual(partial["failed"]["0"], "injected first-item failure")
            self.assertNotIn("1.title", partial["failed"])


if __name__ == "__main__":
    unittest.main()
