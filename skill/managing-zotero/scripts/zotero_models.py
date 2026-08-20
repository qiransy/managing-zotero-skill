from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping


class EvidenceLevel(str, Enum):
    METADATA_ONLY = "metadata_only"
    ABSTRACT_ONLY = "abstract_only"
    FULL_TEXT_VERIFIED = "full_text_verified"
    DEEP_READ = "deep_read"


class MatchKind(str, Enum):
    EXACT_IDENTIFIER = "exact_identifier"
    PROBABLE_BIBLIOGRAPHIC = "probable_bibliographic"
    NONE = "none"


class OperationKind(str, Enum):
    CREATE_COLLECTION = "create_collection"
    UPSERT_ITEMS = "upsert_items"
    CREATE_CHILDREN = "create_children"


@dataclass(frozen=True)
class CandidateItem:
    title: str
    creators: tuple[Mapping[str, str], ...] = ()
    year: str = ""
    publication_title: str = ""
    doi: str = ""
    pmid: str = ""
    arxiv_id: str = ""
    url: str = ""
    abstract: str = ""
    language: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.METADATA_ONLY
    tags: tuple[str, ...] = ()
    linked_pdf: str = ""
    note_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DuplicateMatch:
    kind: MatchKind
    item_key: str = ""
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriteAction:
    kind: str
    payload: Mapping[str, Any]
    item_key: str = ""


@dataclass(frozen=True)
class WritePlan:
    operation: OperationKind
    collection_key: str
    collection_name: str
    actions: tuple[WriteAction, ...]
    expected_versions: Mapping[str, int]
    library_version: int | None = None
    server_fingerprint: str = ""
    duplicate_checks: tuple[Mapping[str, Any], ...] = ()
    allowed_roots: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        action_kinds = tuple(action.kind for action in self.actions)
        if {
            OperationKind.CREATE_COLLECTION.value,
            OperationKind.UPSERT_ITEMS.value,
        }.issubset(action_kinds):
            raise ValueError("collection creation and item upsert require separate WritePlans")


@dataclass(frozen=True)
class ExecutionResult:
    plan_digest: str
    successful_keys: tuple[str, ...] = ()
    unchanged_keys: tuple[str, ...] = ()
    resolved_keys: tuple[str, ...] = ()
    failed: Mapping[str, str] = field(default_factory=dict)
    verified: bool = False


def canonical_json(value: Any) -> str:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_digest(plan: WritePlan) -> str:
    return sha256(canonical_json(plan).encode("utf-8")).hexdigest()
