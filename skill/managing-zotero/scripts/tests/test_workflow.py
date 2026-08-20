import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotero_models import CandidateItem, EvidenceLevel
from zotero_workflow import (
    build_linked_attachment,
    candidate_to_zotero_item,
    render_note,
    sanitize_tags,
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


if __name__ == "__main__":
    unittest.main()
