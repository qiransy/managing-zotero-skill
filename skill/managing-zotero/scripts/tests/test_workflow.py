import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotero_models import CandidateItem, EvidenceLevel, OperationKind, WriteAction, WritePlan, plan_digest
from zotero_workflow import (
    ApprovalProof,
    ApprovalRequired,
    BatchLimitExceeded,
    PreviewStale,
    build_collection_plan,
    build_item_plan,
    build_linked_attachment,
    candidate_to_zotero_item,
    execute_plan,
    render_note,
    sanitize_tags,
    verify_readback,
)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix="workflow-")
        self.approved_root = Path(self.tempdir.name) / "D-approved-library"
        self.approved_root.mkdir()
        self.final_pdf = self.approved_root / "paper.pdf"
        self.final_pdf.write_bytes(b"%PDF-1.7")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_abstract_only_cannot_claim_full_text(self):
        candidate = CandidateItem(
            title="A paper",
            abstract="Abstract text",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
            tags=("状态：全文已核查", "实验：CP-FTMW"),
            note_fields={"exact_constants": "A = 1234.567 MHz", "page": "4"},
        )
        note = render_note(candidate, "microwave-spectroscopy")
        tags = sanitize_tags(candidate, "microwave-spectroscopy")
        self.assertIn("基于摘要", note)
        self.assertNotIn("1234.567", note)
        self.assertNotIn("状态：全文已核查", tags)
        self.assertIn("状态：待获取全文", tags)

    def test_generic_profile_does_not_add_microwave_tags(self):
        candidate = CandidateItem(title="A paper", tags=("实验：CP-FTMW",))
        self.assertEqual(sanitize_tags(candidate, "generic"), ())

    def test_linked_pdf_must_be_final_existing_pdf_under_allowed_root(self):
        payload = build_linked_attachment(
            "PARENT01",
            self.final_pdf,
            allowed_roots=(self.approved_root,),
        )
        self.assertEqual(payload["itemType"], "attachment")
        self.assertEqual(payload["linkMode"], "linked_file")
        self.assertEqual(payload["parentItem"], "PARENT01")
        self.assertEqual(payload["path"], str(self.final_pdf.resolve()))

    def test_linked_pdf_rejects_missing_files_non_pdfs_relative_outside_and_tmp_paths(self):
        outside_pdf = Path(self.tempdir.name) / "outside.pdf"
        outside_pdf.write_bytes(b"%PDF-1.7")
        non_pdf = self.approved_root / "paper.txt"
        non_pdf.write_text("not a PDF", encoding="utf-8")
        tmp_directory = self.approved_root / "project" / "tmp"
        tmp_directory.mkdir(parents=True)
        tmp_pdf = tmp_directory / "paper.pdf"
        tmp_pdf.write_bytes(b"%PDF-1.7")
        cases = (
            self.approved_root / "missing.pdf",
            non_pdf,
            Path("relative.pdf"),
            outside_pdf,
            tmp_pdf,
        )
        for path in cases:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    build_linked_attachment("PARENT01", path, (self.approved_root,))

    def test_note_escapes_source_text_and_uses_stable_codex_marker(self):
        candidate = CandidateItem(
            title="<source title>",
            evidence_level=EvidenceLevel.FULL_TEXT_VERIFIED,
            note_fields={"relevance": "<script>alert(1)</script>"},
        )
        note = render_note(candidate, "generic", "D:\\reports\\<report>.md")
        self.assertIn("Codex｜文献卡", note)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", note)
        self.assertNotIn("<script>", note)
        self.assertIn("data-codex-note=\"evidence-bounded-v1\"", note)

    def test_abstract_only_does_not_promote_unclassified_constant_fields(self):
        candidate = CandidateItem(
            title="A paper",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
            note_fields={"A": "1234.567 MHz", "assignment_basis": "abstract assignment"},
        )
        note = render_note(candidate, "microwave-spectroscopy")
        self.assertNotIn("1234.567", note)
        self.assertNotIn("abstract assignment", note)

    def test_abstract_only_redacts_prohibited_content_from_all_rendered_source_paths(self):
        prohibited_text = 'A = 1234.567 MHz "quoted" p. 4'
        source_cases = (
            {"abstract": prohibited_text},
            {"tags": ("实验：" + prohibited_text,)},
            {"note_fields": {"relevance": prohibited_text}},
            {"note_fields": {"experiment": prohibited_text}},
            {"note_fields": {"structure": prohibited_text}},
            {"note_fields": {"theory_and_assignment": prohibited_text}},
            {"note_fields": {"use_and_limits": prohibited_text}},
        )
        for source in source_cases:
            with self.subTest(source=source):
                note = render_note(
                    CandidateItem(
                        title="A paper",
                        evidence_level=EvidenceLevel.ABSTRACT_ONLY,
                        **source,
                    ),
                    "microwave-spectroscopy",
                )
                for prohibited in ("1234.567", "quoted", "p. 4"):
                    self.assertNotIn(prohibited, note)

    def test_status_tag_is_canonical_from_evidence_level_not_caller_input(self):
        cases = (
            (EvidenceLevel.ABSTRACT_ONLY, "状态：深度精读", "状态：待获取全文"),
            (EvidenceLevel.FULL_TEXT_VERIFIED, "状态：深度精读", "状态：全文已核查"),
            (EvidenceLevel.DEEP_READ, "状态：全文已核查", "状态：深度精读"),
        )
        for evidence_level, supplied, expected in cases:
            with self.subTest(evidence_level=evidence_level):
                tags = sanitize_tags(
                    CandidateItem(title="A paper", evidence_level=evidence_level, tags=(supplied,)),
                    "microwave-spectroscopy",
                )
                self.assertEqual(tags, (expected,))

    def test_generic_note_does_not_consume_microwave_experiment_tag(self):
        candidate = CandidateItem(title="A paper", tags=("实验：CP-FTMW",))
        note = render_note(candidate, "generic")
        self.assertNotIn("实验：CP-FTMW", note)
        self.assertNotIn("CP-FTMW", note)

    def test_low_evidence_note_field_blocks_each_claim_category(self):
        for claim in self._prohibited_low_evidence_claims():
            with self.subTest(claim=claim):
                note = render_note(
                    CandidateItem(
                        title="A paper",
                        evidence_level=EvidenceLevel.ABSTRACT_ONLY,
                        note_fields={"relevance": claim},
                    ),
                    "microwave-spectroscopy",
                )
                self.assertNotIn(claim, note)

    def test_low_evidence_abstract_fallback_blocks_each_claim_category(self):
        for claim in self._prohibited_low_evidence_claims():
            with self.subTest(claim=claim):
                note = render_note(
                    CandidateItem(
                        title="A paper",
                        abstract=claim,
                        evidence_level=EvidenceLevel.ABSTRACT_ONLY,
                    ),
                    "microwave-spectroscopy",
                )
                self.assertNotIn(claim, note)

    def test_low_evidence_tag_derived_note_text_blocks_each_claim_category(self):
        for claim in self._prohibited_low_evidence_claims():
            tag = "实验：" + claim
            with self.subTest(claim=claim):
                note = render_note(
                    CandidateItem(
                        title="A paper",
                        tags=(tag,),
                        evidence_level=EvidenceLevel.ABSTRACT_ONLY,
                    ),
                    "microwave-spectroscopy",
                )
                self.assertNotIn(claim, note)

    def test_low_evidence_sanitized_tags_block_each_claim_category(self):
        for claim in self._prohibited_low_evidence_claims():
            tag = "证据：" + claim
            with self.subTest(claim=claim):
                tags = sanitize_tags(
                    CandidateItem(
                        title="A paper",
                        tags=(tag,),
                        evidence_level=EvidenceLevel.ABSTRACT_ONLY,
                    ),
                    "microwave-spectroscopy",
                )
                self.assertNotIn(tag, tags)

    def test_low_evidence_preserves_legitimate_controlled_tags(self):
        tags = sanitize_tags(
            CandidateItem(
                title="A paper",
                tags=("实验：CP-FTMW",),
                evidence_level=EvidenceLevel.ABSTRACT_ONLY,
            ),
            "microwave-spectroscopy",
        )
        self.assertIn("实验：CP-FTMW", tags)

    def test_full_text_and_deep_read_preserve_exact_assignments(self):
        for evidence_level in (EvidenceLevel.FULL_TEXT_VERIFIED, EvidenceLevel.DEEP_READ):
            with self.subTest(evidence_level=evidence_level):
                candidate = CandidateItem(
                    title="A paper",
                    evidence_level=evidence_level,
                    note_fields={"relevance": "A = 1234.567; μa = 1.2 D"},
                    tags=("证据：A = 1234.567",),
                )
                note = render_note(candidate, "microwave-spectroscopy")
                tags = sanitize_tags(candidate, "microwave-spectroscopy")
                self.assertIn("A = 1234.567", note)
                self.assertIn("μa = 1.2 D", note)
                self.assertIn("证据：A = 1234.567", tags)

    @staticmethod
    def _prohibited_low_evidence_claims():
        return (
            "A: 1234.567 MHz",
            "A = 1234.567",
            "μa = 1.2 D",
            "pp. 4–5",
            "「quoted」",
        )

    def test_item_payload_has_bibliographic_data_without_overwriting_notes(self):
        candidate = CandidateItem(
            title="A paper",
            creators=({"firstName": "Ada", "lastName": "Lovelace"},),
            year="2026",
            publication_title="Journal",
            doi="10.1000/example",
            tags=("实验：CP-FTMW",),
        )
        payload = candidate_to_zotero_item(candidate, "COLLECT01", "microwave-spectroscopy")
        self.assertEqual(payload["itemType"], "journalArticle")
        self.assertEqual(payload["collections"], ["COLLECT01"])
        self.assertEqual(payload["tags"], [{"tag": "实验：CP-FTMW"}, {"tag": "状态：待获取全文"}])
        self.assertNotIn("notes", payload)


class PreviewAndExecutionTests(unittest.TestCase):
    """Approval-bound write behavior, using only an in-memory local API fake."""

    def setUp(self):
        self.client = _FakeWorkflowClient()
        self.collection = {"key": "COLLECT01", "version": 4}

    def candidates(self, count=1):
        return tuple(
            CandidateItem(title=f"Paper {number}", doi=f"10.1000/{number}")
            for number in range(count)
        )

    def item_plan(self, expected_versions=None):
        plan = build_item_plan(self.candidates(), self.collection, [])
        if expected_versions is None:
            return plan
        return WritePlan(
            operation=plan.operation,
            collection_key=plan.collection_key,
            collection_name=plan.collection_name,
            actions=plan.actions,
            expected_versions=expected_versions,
            library_version=plan.library_version,
        )

    def test_execute_refuses_missing_user_confirmation(self):
        plan = self.item_plan()
        proof = ApprovalProof(digest=plan_digest(plan), user_confirmed=False)
        with self.assertRaises(ApprovalRequired):
            execute_plan(self.client, plan, proof)
        self.assertEqual(self.client.authorization_calls, 0)
        self.assertEqual(self.client.write_calls, 0)

    def test_execute_refuses_changed_preview_digest(self):
        plan = self.item_plan()
        with self.assertRaises(ApprovalRequired):
            execute_plan(self.client, plan, ApprovalProof("0" * 64, True))
        self.assertEqual(self.client.write_calls, 0)

    def test_version_change_stops_before_authorization(self):
        plan = self.item_plan(expected_versions={"ITEM0001": 7})
        self.client.current_versions = {"ITEM0001": 8}
        with self.assertRaises(PreviewStale):
            execute_plan(self.client, plan, ApprovalProof(plan_digest(plan), True))
        self.assertEqual(self.client.authorization_calls, 0)

    def test_collection_version_is_reread_from_the_collection_endpoint(self):
        plan = self.item_plan()
        client = _HttpOnlyVersionClient()
        with self.assertRaises(PreviewStale):
            execute_plan(client, plan, ApprovalProof(plan_digest(plan), True))
        self.assertEqual(client.paths, ["users/0/collections/COLLECT01"])
        self.assertEqual(client.authorization_calls, 0)

    def test_item_plan_rejects_eleven_papers(self):
        with self.assertRaises(BatchLimitExceeded):
            build_item_plan(self.candidates(11), self.collection, [])

    def test_exact_duplicate_reuses_item_without_clearing_tags_or_other_collections(self):
        existing = [{
            "key": "ITEM0001",
            "version": 7,
            "data": {"DOI": "10.1000/0", "collections": ["OTHER001"], "tags": [{"tag": "personal"}]},
        }]
        plan = build_item_plan(self.candidates(), self.collection, existing)
        payload = plan.actions[0].payload[0]
        self.assertEqual(payload["key"], "ITEM0001")
        self.assertEqual(payload["collections"], ["OTHER001", "COLLECT01"])
        self.assertNotIn("tags", payload)

    def test_collection_creation_and_item_upsert_cannot_share_a_plan(self):
        with self.assertRaises(ValueError):
            WritePlan(
                operation=OperationKind.UPSERT_ITEMS,
                collection_key="COLLECT01",
                collection_name="Research",
                actions=(
                    WriteAction("create_collection", {"name": "Research"}),
                    WriteAction("upsert_items", []),
                ),
                expected_versions={},
            )

    def test_collection_plan_contains_one_collection_action(self):
        plan = build_collection_plan("Research", library_version=9)
        self.assertEqual(plan.operation, OperationKind.CREATE_COLLECTION)
        self.assertEqual(tuple(action.kind for action in plan.actions), ("create_collection",))
        self.assertEqual(plan.library_version, 9)

    def test_collection_readback_reports_a_changed_approved_name(self):
        plan = build_collection_plan("Research", library_version=9)
        verified, mismatches = verify_readback(plan, {"COLLECT01": {"name": "Changed"}})
        self.assertFalse(verified)
        self.assertIn("COLLECT01.name", mismatches)

    def test_partial_write_reports_success_unchanged_and_failed_without_retry(self):
        plan = self.item_plan()
        self.client.write_response = {
            "successful": {"0": "ITEM0001"},
            "unchanged": {"1": "ITEM0002"},
            "failed": {"2": {"message": "invalid item"}},
        }
        self.client.fetched_objects = {"ITEM0001": plan.actions[0].payload[0]}
        result = execute_plan(self.client, plan, ApprovalProof(plan_digest(plan), True))
        self.assertEqual(result.successful_keys, ("ITEM0001",))
        self.assertEqual(result.unchanged_keys, ("ITEM0002",))
        self.assertEqual(result.failed, {"2": "invalid item"})
        self.assertEqual(self.client.write_calls, 1)
        self.assertFalse(result.verified)

    def test_verify_readback_accepts_approved_fields_and_ignores_personal_note(self):
        plan = self.item_plan()
        approved = plan.actions[0].payload[0]
        fetched = {
            "ITEM0001": {
                **approved,
                "key": "ITEM0001",
                "notes": [{"note": "A personal note"}],
                "extra": "unrelated field",
            }
        }
        verified, mismatches = verify_readback(plan, fetched)
        self.assertTrue(verified)
        self.assertEqual(mismatches, {})

    def test_verify_readback_reports_changed_approved_title(self):
        plan = self.item_plan()
        approved = plan.actions[0].payload[0]
        verified, mismatches = verify_readback(
            plan,
            {"ITEM0001": {**approved, "key": "ITEM0001", "title": "Changed"}},
        )
        self.assertFalse(verified)
        self.assertIn("ITEM0001.title", mismatches)

    def test_verify_readback_checks_codex_note_marker_and_linked_attachment_path(self):
        plan = WritePlan(
            operation=OperationKind.UPSERT_ITEMS,
            collection_key="COLLECT01",
            collection_name="Research",
            actions=(WriteAction("upsert_items", (
                {"itemType": "journalArticle", "key": "ITEM0001", "title": "Paper", "DOI": "10.1000/0", "collections": ["COLLECT01"], "tags": []},
                {"itemType": "note", "key": "NOTE0001", "parentItem": "ITEM0001", "note": '<div data-codex-note="evidence-bounded-v1">Codex</div>'},
                {"itemType": "attachment", "key": "ATTACH01", "parentItem": "ITEM0001", "linkMode": "linked_file", "path": "D:\\library\\paper.pdf"},
            )),),
            expected_versions={},
        )
        verified, mismatches = verify_readback(plan, {
            "ITEM0001": {"title": "Paper", "DOI": "10.1000/0", "collections": ["COLLECT01"], "tags": []},
            "NOTE0001": {"note": "missing marker"},
            "ATTACH01": {"path": "D:\\library\\other.pdf"},
        })
        self.assertFalse(verified)
        self.assertIn("NOTE0001.child_note", mismatches)
        self.assertIn("ATTACH01.attachment_path", mismatches)


class _FakeWorkflowClient:
    def __init__(self):
        self.authorization_calls = 0
        self.write_calls = 0
        self.discard_calls = 0
        self.current_versions = {"COLLECT01": 4}
        self.write_response = {"successful": {"0": "ITEM0001"}, "unchanged": {}, "failed": {}}
        self.fetched_objects = {"ITEM0001": {"title": "Paper 0", "DOI": "10.1000/0", "collections": ["COLLECT01"], "tags": []}}

    def get_versions(self, keys):
        return {key: self.current_versions.get(key) for key in keys}

    def authorize_once(self):
        self.authorization_calls += 1
        return object()

    def discard_authorization(self, authorization):
        self.discard_calls += 1

    def post_json(self, path, payload, authorization, expected_version=None):
        self.write_calls += 1
        return self.write_response, None

    def fetch_objects(self, keys):
        return {key: self.fetched_objects[key] for key in keys if key in self.fetched_objects}


class _HttpOnlyVersionClient:
    def __init__(self):
        self.paths = []
        self.authorization_calls = 0

    def get_json(self, path):
        self.paths.append(path)
        return {"version": 5}, None

    def authorize_once(self):
        self.authorization_calls += 1
        return object()


if __name__ == "__main__":
    unittest.main()
