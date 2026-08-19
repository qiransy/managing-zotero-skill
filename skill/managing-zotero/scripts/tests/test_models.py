import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotero_models import (
    CandidateItem,
    EvidenceLevel,
    OperationKind,
    WriteAction,
    WritePlan,
    plan_digest,
)


class ModelTests(unittest.TestCase):
    def test_candidate_is_immutable(self):
        item = CandidateItem(title="Water dimer", doi="10.1000/example")
        with self.assertRaises(FrozenInstanceError):
            item.title = "changed"

    def test_digest_is_stable_for_equivalent_plans(self):
        action = WriteAction(kind="create_item", payload={"title": "A"})
        first = WritePlan(
            operation=OperationKind.UPSERT_ITEMS,
            collection_key="ABCD2345",
            collection_name="system",
            actions=(action,),
            expected_versions={},
        )
        second = WritePlan(
            operation=OperationKind.UPSERT_ITEMS,
            collection_key="ABCD2345",
            collection_name="system",
            actions=(action,),
            expected_versions={},
        )
        self.assertEqual(plan_digest(first), plan_digest(second))
        self.assertEqual(len(plan_digest(first)), 64)

    def test_evidence_levels_are_explicit(self):
        self.assertEqual(EvidenceLevel.ABSTRACT_ONLY.value, "abstract_only")
        self.assertEqual(EvidenceLevel.FULL_TEXT_VERIFIED.value, "full_text_verified")


if __name__ == "__main__":
    unittest.main()
