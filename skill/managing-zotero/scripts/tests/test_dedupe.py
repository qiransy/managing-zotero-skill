import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotero_dedupe import classify_duplicate, normalize_title
from zotero_models import CandidateItem, MatchKind


class DuplicateClassificationTests(unittest.TestCase):
    def test_title_normalization_removes_quotes_after_outer_whitespace(self):
        self.assertEqual(normalize_title('  “Water complex”  '), "water complex")

    def test_doi_url_and_plain_doi_are_exact_duplicates(self):
        candidate = CandidateItem(title="Different", doi="https://doi.org/10.1000/ABC")
        existing = [{"key": "ITEM0001", "data": {"DOI": "10.1000/abc"}}]

        match = classify_duplicate(candidate, existing)

        self.assertEqual(match.kind, MatchKind.EXACT_IDENTIFIER)
        self.assertEqual(match.item_key, "ITEM0001")
        self.assertEqual(match.reasons, ("doi",))

    def test_pmid_in_extra_is_an_exact_identifier_duplicate(self):
        candidate = CandidateItem(title="Different", pmid="12345678")
        existing = [{"key": "ITEM0004", "data": {"extra": "PMID: 12345678"}}]

        match = classify_duplicate(candidate, existing)

        self.assertEqual(match.kind, MatchKind.EXACT_IDENTIFIER)
        self.assertEqual(match.item_key, "ITEM0004")
        self.assertEqual(match.reasons, ("pmid",))

    def test_arxiv_in_extra_is_an_exact_identifier_duplicate(self):
        candidate = CandidateItem(title="Different", arxiv_id="arXiv:2401.01234v2")
        existing = [{"key": "ITEM0005", "data": {"extra": "arXiv: 2401.01234v2"}}]

        match = classify_duplicate(candidate, existing)

        self.assertEqual(match.kind, MatchKind.EXACT_IDENTIFIER)
        self.assertEqual(match.item_key, "ITEM0005")
        self.assertEqual(match.reasons, ("arxiv",))

    def test_unversioned_arxiv_matches_a_versioned_extra_identifier(self):
        candidate = CandidateItem(title="Different", arxiv_id="2401.01234")
        existing = [{"key": "ITEM0008", "data": {"extra": "arXiv: 2401.01234v2"}}]

        match = classify_duplicate(candidate, existing)

        self.assertEqual(match.kind, MatchKind.EXACT_IDENTIFIER)
        self.assertEqual(match.item_key, "ITEM0008")
        self.assertEqual(match.reasons, ("arxiv",))

    def test_unversioned_legacy_arxiv_matches_a_versioned_dotted_category_identifier(self):
        candidate = CandidateItem(title="Different", arxiv_id="math.GT/0309136")
        existing = [{"key": "ITEM0009", "data": {"extra": "arXiv: math.GT/0309136v2"}}]

        match = classify_duplicate(candidate, existing)

        self.assertEqual(match.kind, MatchKind.EXACT_IDENTIFIER)
        self.assertEqual(match.item_key, "ITEM0009")
        self.assertEqual(match.reasons, ("arxiv",))

    def test_title_author_year_is_probable_not_automatic_merge(self):
        candidate = CandidateItem(
            title="The ethanolamine-water complex",
            year="2026",
            creators=({"lastName": "Chen", "firstName": "Yu"},),
        )
        existing = [{"key": "ITEM0002", "data": {
            "title": "The Ethanolamine–Water Complex",
            "date": "2026",
            "creators": [{"lastName": "Chen", "firstName": "Yu"}],
        }}]

        match = classify_duplicate(candidate, existing)

        self.assertEqual(match.kind, MatchKind.PROBABLE_BIBLIOGRAPHIC)
        self.assertEqual(match.item_key, "ITEM0002")
        self.assertEqual(match.reasons, ("title_author_year",))

    def test_ambiguous_bibliographic_matches_are_not_silently_deduplicated(self):
        candidate = CandidateItem(
            title="Water complex",
            year="2026",
            creators=({"lastName": "Chen"},),
        )
        existing = [
            {"key": "ITEM0006", "data": {"title": "Water Complex", "date": "2026", "creators": [{"lastName": "Chen"}]}},
            {"key": "ITEM0007", "data": {"title": "Water Complex", "date": "2026", "creators": [{"lastName": "Chen"}]}},
        ]

        match = classify_duplicate(candidate, existing)

        self.assertEqual(match.kind, MatchKind.PROBABLE_BIBLIOGRAPHIC)
        self.assertEqual(match.item_key, "")
        self.assertEqual(match.reasons, ("title_author_year", "ambiguous"))

    def test_unrelated_item_is_not_a_duplicate(self):
        match = classify_duplicate(
            CandidateItem(title="Water dimer", year="2024"),
            [{"key": "ITEM0003", "data": {"title": "Methanol trimer", "date": "2024"}}],
        )

        self.assertEqual(match.kind, MatchKind.NONE)
        self.assertEqual(match.item_key, "")
        self.assertEqual(match.reasons, ())


if __name__ == "__main__":
    unittest.main()
