"""Pure payload builders for an evidence-bounded Zotero workflow."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import escape
from pathlib import Path
import re
import secrets
from typing import Any, Iterable, Mapping
import warnings

from zotero_audit import AuditEvent, append_audit_event
from zotero_dedupe import classify_duplicate
from zotero_models import (
    CandidateItem,
    EvidenceLevel,
    ExecutionResult,
    MatchKind,
    OperationKind,
    WriteAction,
    WritePlan,
    plan_digest,
)


_MICROWAVE_PROFILE = "microwave-spectroscopy"
_GENERIC_PROFILE = "generic"
_ALLOWED_MICROWAVE_PREFIXES = ("体系：", "实验：", "证据：", "计算：", "状态：")
_FULL_TEXT_LEVELS = frozenset((EvidenceLevel.FULL_TEXT_VERIFIED, EvidenceLevel.DEEP_READ))
_UNVERIFIED_FIELD_TOKENS = ("constant", "quotation", "quote", "page")
_UNVERIFIED_SAFE_FIELDS = frozenset(("relevance", "experiment", "structure", "theory_and_assignment", "use_and_limits"))
_UNVERIFIED_CLAIM = re.compile(
    r"(?:[^\s:=]+\s*[:=]\s*[+-]?\d+(?:[.,]\d+)?"
    r"|\b[+-]?\d+(?:[.,]\d+)?\s*(?:GHz|MHz|kHz|Hz|D\b|cm\s*\^?-?1)"
    r"|\b(?:p|pp|page|pages)\.?\s*\d+(?:\s*(?:-|–|—)\s*\d+)?"
    r"|第?\s*\d+(?:\s*(?:-|–|—|至|到)\s*\d+)?\s*页"
    r"|[\"“”‘’「」『』])",
    re.IGNORECASE,
)
_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "assets" / "zotero-brief-note-template.html"
_MAX_PAPERS_PER_BATCH = 10
_ZOTERO_KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
_CODEX_NOTE_MARKER = 'data-codex-note="evidence-bounded-v1"'
_PARENT_CREATE_FIELDS = frozenset((
    "itemType", "key", "version", "title", "creators", "date", "publicationTitle",
    "DOI", "url", "abstractNote", "language", "collections", "tags", "extra",
))
_PARENT_REUSE_FIELDS = frozenset(("itemType", "key", "version", "collections"))
_NOTE_FIELDS = frozenset(("itemType", "key", "version", "parentItem", "note"))
_ATTACHMENT_FIELDS = frozenset(("itemType", "key", "version", "parentItem", "linkMode", "contentType", "path", "title"))
_DESTRUCTIVE_FIELDS = frozenset(("deleted", "trash", "trashed", "dateDeleted"))


class ApprovalRequired(RuntimeError):
    """A user did not explicitly approve the exact preview being applied."""


class PreviewStale(RuntimeError):
    """A version changed after preview and before authorization."""


class BatchLimitExceeded(ValueError):
    """A plan exceeds the conservative first-version batch boundary."""


class DuplicateConflict(ValueError):
    """A probable duplicate requires manual resolution and cannot be written."""


@dataclass(frozen=True)
class ApprovalProof:
    digest: str
    user_confirmed: bool


def server_fingerprint(server_id: str) -> str:
    value = str(server_id)
    return sha256(value.encode("utf-8")).hexdigest() if value else ""


def build_collection_plan(name: str, library_version: int, *, server_id: str = "") -> WritePlan:
    """Build the separately-approved, single-collection creation preview."""
    collection_name = str(name).strip()
    if not collection_name:
        raise ValueError("collection name is required")
    if not isinstance(library_version, int) or library_version < 0:
        raise ValueError("library_version must be a non-negative integer")
    return WritePlan(
        operation=OperationKind.CREATE_COLLECTION,
        collection_key="",
        collection_name=collection_name,
        actions=(WriteAction("create_collection", {"name": collection_name}),),
        expected_versions={},
        library_version=library_version,
        server_fingerprint=server_fingerprint(server_id),
    )


def build_item_plan(
    candidates: Iterable[CandidateItem],
    collection: Mapping[str, Any],
    existing_items: Iterable[Mapping[str, Any]],
    *,
    server_id: str = "",
) -> WritePlan:
    """Build one bounded item-upsert preview for an already-existing Collection."""
    candidate_list = tuple(candidates)
    if len(candidate_list) > _MAX_PAPERS_PER_BATCH:
        raise BatchLimitExceeded("an item plan may contain at most 10 candidate papers")
    collection_key = str(collection.get("key", ""))
    if not collection_key:
        raise ValueError("an item plan requires an existing collection key")
    existing = tuple(existing_items)
    expected_versions: dict[str, int] = {}
    collection_version = _object_version(collection)
    if collection_version is not None:
        expected_versions[collection_key] = collection_version
    payloads: list[dict[str, Any]] = []
    duplicate_checks: list[dict[str, Any]] = []
    for candidate in candidate_list:
        if not isinstance(candidate, CandidateItem):
            raise TypeError("candidates must be CandidateItem instances")
        payload = candidate_to_zotero_item(candidate, collection_key, _GENERIC_PROFILE)
        payload["key"] = _new_zotero_key()
        payload["version"] = 0
        duplicate = classify_duplicate(candidate, existing)
        duplicate_checks.append(_duplicate_check(candidate, duplicate))
        if duplicate.kind == MatchKind.EXACT_IDENTIFIER and duplicate.item_key:
            matched = next((item for item in existing if str(item.get("key", "")) == duplicate.item_key), {})
            existing_data = _object_data(matched)
            version = _object_version(matched)
            if version is None:
                raise ValueError("an existing duplicate requires a version")
            expected_versions[duplicate.item_key] = version
            existing_collections = [str(key) for key in existing_data.get("collections", ())]
            payload = {
                "itemType": str(existing_data.get("itemType", "journalArticle")),
                "key": duplicate.item_key,
                "version": version,
                "collections": list(dict.fromkeys((*existing_collections, collection_key))),
            }
        elif duplicate.kind == MatchKind.PROBABLE_BIBLIOGRAPHIC:
            raise DuplicateConflict("probable bibliographic duplicate requires manual confirmation")
        payloads.append(payload)
    return WritePlan(
        operation=OperationKind.UPSERT_ITEMS,
        collection_key=collection_key,
        collection_name=str(collection.get("name", "")),
        actions=(WriteAction("upsert_items", tuple(payloads)),),
        expected_versions=expected_versions,
        library_version=None,
        server_fingerprint=server_fingerprint(server_id),
        duplicate_checks=tuple(duplicate_checks),
    )


def execute_plan(
    client: Any,
    plan: WritePlan,
    proof: ApprovalProof,
    audit_dir: str | Path | None = None,
) -> ExecutionResult:
    """Apply exactly one reviewed request, read it back, and audit the result."""
    _validate_plan(plan)
    if not proof.user_confirmed:
        raise ApprovalRequired("explicit user confirmation is required")
    calculated_digest = plan_digest(plan)
    if (
        not isinstance(proof.digest, str)
        or len(proof.digest) != 64
        or not secrets.compare_digest(calculated_digest, proof.digest)
    ):
        raise ApprovalRequired("the approved preview digest does not match this plan")
    _ensure_server_identity(client, plan)
    _ensure_duplicate_state_unchanged(client, plan)
    _ensure_versions_unchanged(client, plan)

    authorization = client.authorize_once()
    try:
        try:
            response, _ = client.post_json(
                _write_path(plan),
                _write_payload(plan),
                authorization,
                expected_version=plan.library_version,
            )
        except Exception as exc:
            _audit_attempt(
                audit_dir,
                plan,
                calculated_digest,
                outcome="error",
                failed={"mutation": type(exc).__name__},
                verified=None,
                verification_status="not_available",
            )
            raise
    finally:
        discard = getattr(client, "discard_authorization", None)
        if callable(discard):
            discard(authorization)

    successful, unchanged, failed = _parse_write_response(response, plan)
    affected_keys = tuple(dict.fromkeys((*successful, *unchanged)))
    try:
        fetched = _fetch_objects(client, plan, affected_keys)
        readback_ok, mismatches = _verify_response_readback(plan, response, fetched)
    except Exception as exc:
        _audit_attempt(
            audit_dir,
            plan,
            calculated_digest,
            outcome="readback_error",
            successful=successful,
            unchanged=unchanged,
            failed=failed | {"readback": type(exc).__name__},
            verified=None,
            verification_status="error",
        )
        raise
    if failed:
        readback_ok = False
    result = ExecutionResult(
        plan_digest=calculated_digest,
        successful_keys=successful,
        unchanged_keys=unchanged,
        failed=failed | mismatches,
        verified=readback_ok,
    )
    _audit_attempt(
        audit_dir,
        plan,
        calculated_digest,
        outcome=_audit_outcome(result),
        successful=result.successful_keys,
        unchanged=result.unchanged_keys,
        failed=result.failed,
        verified=result.verified,
        verification_status="verified" if result.verified else "mismatch",
    )
    return result


def verify_readback(plan: WritePlan, fetched_objects: Mapping[str, Any]) -> tuple[bool, dict[str, str]]:
    """Compare only approved fields, leaving user-owned fields deliberately free."""
    mismatches: dict[str, str] = {}
    if plan.operation == OperationKind.CREATE_COLLECTION:
        expected_name = str(plan.actions[0].payload.get("name", ""))
        for key, value in fetched_objects.items():
            actual = _object_data(value)
            if actual.get("name") != expected_name:
                mismatches[f"{key}.name"] = "approved collection name differs from read-back"
        if not fetched_objects:
            mismatches["collection.object"] = "collection was not returned by read-back"
        return not mismatches, mismatches
    expected_items = _approved_payloads(plan)
    fetched_by_key = {str(key): _object_data(value) for key, value in fetched_objects.items()}
    used_keys: set[str] = set()
    for position, expected in enumerate(expected_items):
        key = str(expected.get("key", ""))
        if key:
            actual = fetched_by_key.get(key)
        else:
            remaining = ((candidate_key, value) for candidate_key, value in fetched_by_key.items() if candidate_key not in used_keys)
            key, actual = next(remaining, ("", None))
        label = key or str(position)
        if not isinstance(actual, Mapping):
            mismatches[f"{label}.object"] = "object was not returned by read-back"
            continue
        used_keys.add(key)
        _compare_item_fields(expected, actual, label, mismatches)
    return not mismatches, mismatches


def _audit_attempt(
    audit_dir: str | Path | None,
    plan: WritePlan,
    digest: str,
    *,
    outcome: str,
    successful: tuple[str, ...] = (),
    unchanged: tuple[str, ...] = (),
    failed: Mapping[str, str] | None = None,
    verified: bool | None,
    verification_status: str,
) -> None:
    """Record an attempt without ever retrying or replacing its known outcome."""
    if audit_dir is None:
        return
    event = AuditEvent(
        plan_digest=digest,
        operation=plan.operation.value,
        outcome=outcome,
        target_collection={"itemKey": plan.collection_key, "name": plan.collection_name},
        approved_action_count=len(plan.actions),
        approved_actions=tuple(action.kind for action in plan.actions),
        successful_item_keys=successful,
        unchanged_item_keys=unchanged,
        failed=dict(failed or {}),
        verified=verified,
        details={"verification_status": verification_status},
    )
    try:
        append_audit_event(audit_dir, event)
    except (OSError, ValueError) as exc:
        warnings.warn(
            f"Zotero mutation outcome is available, but its audit record could not be written: {type(exc).__name__}",
            RuntimeWarning,
            stacklevel=2,
        )


def _audit_outcome(result: ExecutionResult) -> str:
    if result.failed:
        return "partial" if result.successful_keys or result.unchanged_keys else "failed"
    return "success" if result.verified else "unverified"


def _validate_plan(plan: WritePlan) -> None:
    action_kinds = tuple(action.kind for action in plan.actions)
    if plan.operation == OperationKind.CREATE_COLLECTION:
        if action_kinds != ("create_collection",):
            raise ValueError("a collection plan must contain exactly one collection creation")
        payload = plan.actions[0].payload
        if not isinstance(payload, Mapping) or set(payload) != {"name"} or not str(payload.get("name", "")).strip():
            raise ValueError("collection payload contains unsafe or unknown fields")
    elif plan.operation == OperationKind.UPSERT_ITEMS:
        if action_kinds != ("upsert_items",) or not plan.collection_key:
            raise ValueError("an item plan must target one existing collection")
        if len(_item_payloads(plan)) > _MAX_PAPERS_PER_BATCH:
            raise BatchLimitExceeded("an item plan may contain at most 10 candidate papers")
        if plan.collection_key not in plan.expected_versions:
            raise ValueError("the target collection requires a bound version")
        payloads = _approved_payloads(plan)
        if not payloads:
            raise ValueError("an item plan requires at least one payload")
        if len(plan.duplicate_checks) != len(_item_payloads(plan)):
            raise ValueError("every top-level paper requires a bound duplicate check")
        for payload in payloads:
            _validate_item_payload(plan, payload)
    else:
        raise ValueError("unknown write plan operation")


def _validate_item_payload(plan: WritePlan, payload: Mapping[str, Any]) -> None:
    if _DESTRUCTIVE_FIELDS.intersection(payload):
        raise ValueError("destructive Zotero fields are forbidden")
    item_type = str(payload.get("itemType", ""))
    key = str(payload.get("key", ""))
    version = payload.get("version")
    if not key or not isinstance(version, int) or version < 0:
        raise ValueError("every planned Zotero object requires a key and non-negative version")
    if version > 0 and plan.expected_versions.get(key) != version:
        raise ValueError("every existing Zotero object requires its approved version")
    if item_type == "journalArticle":
        allowed = _PARENT_REUSE_FIELDS if version > 0 else _PARENT_CREATE_FIELDS
        if not set(payload).issubset(allowed):
            raise ValueError("journal item payload contains unsafe or unknown mutation fields")
        if version > 0 and "tags" in payload:
            raise ValueError("existing Zotero tags cannot be cleared or replaced")
        if version == 0 and not str(payload.get("title", "")).strip():
            raise ValueError("new journal items require a title")
        collections = payload.get("collections")
        if not isinstance(collections, (list, tuple)) or plan.collection_key not in map(str, collections):
            raise ValueError("journal item must retain the approved Collection membership")
        if "tags" in payload and not isinstance(payload["tags"], (list, tuple)):
            raise ValueError("tags must be an explicit list")
        return
    if item_type == "note":
        if not set(payload).issubset(_NOTE_FIELDS) or _CODEX_NOTE_MARKER not in str(payload.get("note", "")):
            raise ValueError("only stable-marker Codex notes may be created or updated")
        if version != 0:
            raise ValueError("existing Zotero notes cannot be updated by this version")
        if not str(payload.get("parentItem", "")):
            raise ValueError("Codex notes require a parent item")
        return
    if item_type == "attachment":
        if not set(payload).issubset(_ATTACHMENT_FIELDS):
            raise ValueError("attachment payload contains unsafe or unknown fields")
        if payload.get("linkMode") != "linked_file" or payload.get("contentType") != "application/pdf":
            raise ValueError("only linked PDF attachments are allowed")
        rebuilt = build_linked_attachment(str(payload.get("parentItem", "")), str(payload.get("path", "")), plan.allowed_roots)
        if rebuilt["path"] != payload.get("path"):
            raise ValueError("linked PDF path changed after preview")
        return
    raise ValueError("unsafe or unknown Zotero item type")


def _ensure_server_identity(client: Any, plan: WritePlan) -> None:
    probe = getattr(client, "probe", None)
    if not callable(probe):
        return
    if not re.fullmatch(r"[0-9a-f]{64}", plan.server_fingerprint):
        raise PreviewStale("preview is not bound to a Zotero Server ID")
    status = probe()
    if not getattr(status, "connected", False) or not getattr(status, "server_id", ""):
        raise PreviewStale("Zotero Server ID is unavailable")
    if not secrets.compare_digest(server_fingerprint(status.server_id), plan.server_fingerprint):
        raise PreviewStale("Zotero Server ID changed after preview")


def _ensure_duplicate_state_unchanged(client: Any, plan: WritePlan) -> None:
    if plan.operation != OperationKind.UPSERT_ITEMS or not plan.duplicate_checks:
        return
    get_json = getattr(client, "get_json", None)
    if not callable(get_json):
        return
    for check in plan.duplicate_checks:
        if not isinstance(check, Mapping):
            raise PreviewStale("duplicate binding is malformed")
        candidate = _candidate_from_duplicate_check(check)
        query = _duplicate_query(candidate)
        payload, _ = get_json("users/0/items", query)
        items = payload if isinstance(payload, list) else []
        current = classify_duplicate(candidate, items)
        if current.kind.value != str(check.get("match_kind", "")) or current.item_key != str(check.get("item_key", "")):
            raise PreviewStale("library-wide duplicate state changed after preview")


def _ensure_versions_unchanged(client: Any, plan: WritePlan) -> None:
    if plan.expected_versions:
        keys = tuple(plan.expected_versions)
        get_versions = getattr(client, "get_versions", None)
        if callable(get_versions):
            current_versions = get_versions(keys)
        else:
            current_versions = _read_versions_with_client(client, plan, keys)
        for key, expected in plan.expected_versions.items():
            if not isinstance(current_versions, Mapping) or current_versions.get(key) != expected:
                raise PreviewStale(f"preview is stale for {key}")
    if plan.library_version is not None:
        current_library_version = _read_library_version(client)
        if current_library_version != plan.library_version:
            raise PreviewStale("preview is stale for the Zotero library")


def _read_versions_with_client(client: Any, plan: WritePlan, keys: tuple[str, ...]) -> Mapping[str, int | None]:
    get_json = getattr(client, "get_json", None)
    if not callable(get_json):
        raise PreviewStale("client cannot re-read object versions")
    versions: dict[str, int | None] = {}
    for key in keys:
        resource = "collections" if key == plan.collection_key else "items"
        payload, response = get_json(f"users/0/{resource}/{key}")
        versions[key] = _object_version(payload)
        if versions[key] is None:
            versions[key] = _header_version(response)
    return versions


def _read_library_version(client: Any) -> int | None:
    get_library_version = getattr(client, "get_library_version", None)
    if callable(get_library_version):
        return get_library_version()
    get_json = getattr(client, "get_json", None)
    if not callable(get_json):
        raise PreviewStale("client cannot re-read the library version")
    _, response = get_json("users/0/collections")
    return _header_version(response)


def _write_path(plan: WritePlan) -> str:
    return "users/0/collections" if plan.operation == OperationKind.CREATE_COLLECTION else "users/0/items"


def _write_payload(plan: WritePlan) -> object:
    payload = plan.actions[0].payload
    if plan.operation == OperationKind.CREATE_COLLECTION:
        return [payload]
    return payload


def _parse_write_response(response: Any, plan: WritePlan) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str]]:
    if not isinstance(response, Mapping):
        return (), (), {"response": "Zotero returned an invalid multi-write response"}
    payloads = _approved_payloads(plan)
    successful = _response_keys(response.get("successful"), payloads)
    unchanged = _response_keys(response.get("unchanged"), payloads)
    failed = _failure_reasons(response.get("failed"))
    return successful, unchanged, failed


def _verify_response_readback(
    plan: WritePlan,
    response: Any,
    fetched_objects: Mapping[str, Any],
) -> tuple[bool, dict[str, str]]:
    if plan.operation == OperationKind.CREATE_COLLECTION:
        return verify_readback(plan, fetched_objects)
    if not isinstance(response, Mapping):
        return False, {"response": "Zotero returned an invalid multi-write response"}
    payloads = _approved_payloads(plan)
    fetched = {str(key): _object_data(value) for key, value in fetched_objects.items()}
    mismatches: dict[str, str] = {}
    for group_name in ("successful", "unchanged"):
        group = response.get(group_name)
        if not isinstance(group, Mapping):
            continue
        for raw_index, value in group.items():
            try:
                index = int(raw_index)
                expected = payloads[index]
            except (ValueError, IndexError, TypeError):
                mismatches[f"response.{raw_index}"] = "Zotero returned an invalid payload index"
                continue
            key = str(value.get("key", "")) if isinstance(value, Mapping) else str(value)
            if not key:
                key = str(expected.get("key", ""))
            actual = fetched.get(key)
            if not isinstance(actual, Mapping):
                mismatches[f"{key or raw_index}.object"] = "object was not returned by read-back"
                continue
            _compare_item_fields(expected, actual, key or str(raw_index), mismatches)
    return not mismatches, mismatches


def _response_keys(group: Any, payloads: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    if not isinstance(group, Mapping):
        return ()
    keys: list[str] = []
    for raw_index, value in group.items():
        key = ""
        if isinstance(value, Mapping):
            key = str(value.get("key", ""))
        elif isinstance(value, str):
            key = value
        if not key:
            try:
                key = str(payloads[int(raw_index)].get("key", ""))
            except (IndexError, TypeError, ValueError):
                key = ""
        if key:
            keys.append(key)
    return tuple(keys)


def _failure_reasons(group: Any) -> dict[str, str]:
    if not isinstance(group, Mapping):
        return {}
    reasons: dict[str, str] = {}
    for index, detail in group.items():
        if isinstance(detail, Mapping):
            reasons[str(index)] = str(detail.get("message") or detail.get("code") or "write failed")
        else:
            reasons[str(index)] = str(detail)
    return reasons


def _duplicate_check(candidate: CandidateItem, duplicate: Any) -> dict[str, Any]:
    return {
        "title": candidate.title,
        "creators": [dict(creator) for creator in candidate.creators],
        "year": candidate.year,
        "doi": candidate.doi,
        "pmid": candidate.pmid,
        "arxiv_id": candidate.arxiv_id,
        "match_kind": duplicate.kind.value,
        "item_key": duplicate.item_key,
    }


def _candidate_from_duplicate_check(check: Mapping[str, Any]) -> CandidateItem:
    creators = check.get("creators", ())
    if not isinstance(creators, (list, tuple)) or not all(isinstance(value, Mapping) for value in creators):
        raise PreviewStale("duplicate binding creators are malformed")
    return CandidateItem(
        title=str(check.get("title", "")),
        creators=tuple(dict(value) for value in creators),
        year=str(check.get("year", "")),
        doi=str(check.get("doi", "")),
        pmid=str(check.get("pmid", "")),
        arxiv_id=str(check.get("arxiv_id", "")),
    )


def _duplicate_query(candidate: CandidateItem) -> Mapping[str, str]:
    if candidate.doi:
        return {"doi": candidate.doi}
    if candidate.pmid:
        return {"q": "PMID: " + candidate.pmid}
    if candidate.arxiv_id:
        return {"q": "arXiv: " + candidate.arxiv_id}
    return {"q": candidate.title}


def _new_zotero_key() -> str:
    return "".join(secrets.choice(_ZOTERO_KEY_ALPHABET) for _ in range(8))


def _fetch_objects(client: Any, plan: WritePlan, keys: tuple[str, ...]) -> Mapping[str, Any]:
    if not keys:
        return {}
    fetch_objects = getattr(client, "fetch_objects", None)
    if callable(fetch_objects):
        result = fetch_objects(keys)
        return result if isinstance(result, Mapping) else {}
    get_json = getattr(client, "get_json", None)
    if not callable(get_json):
        return {}
    fetched: dict[str, Any] = {}
    resource = "collections" if plan.operation == OperationKind.CREATE_COLLECTION else "items"
    for key in keys:
        payload, _ = get_json(f"users/0/{resource}/{key}")
        fetched[key] = payload
    return fetched


def _item_payloads(plan: WritePlan) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        item
        for item in _approved_payloads(plan)
        if item.get("itemType") not in {"note", "attachment"}
    )


def _approved_payloads(plan: WritePlan) -> tuple[Mapping[str, Any], ...]:
    if plan.operation != OperationKind.UPSERT_ITEMS or not plan.actions:
        return ()
    payload = plan.actions[0].payload
    if isinstance(payload, Mapping):
        return (payload,)
    if isinstance(payload, (list, tuple)):
        return tuple(item for item in payload if isinstance(item, Mapping))
    return ()


def _compare_item_fields(expected: Mapping[str, Any], actual: Mapping[str, Any], label: str, mismatches: dict[str, str]) -> None:
    for field in ("title", "DOI"):
        if field in expected and actual.get(field) != expected[field]:
            mismatches[f"{label}.{field}"] = "approved value differs from read-back"
    if "collections" in expected:
        expected_collections = set(map(str, expected["collections"]))
        actual_collections = set(map(str, actual.get("collections", ())))
        if not expected_collections.issubset(actual_collections):
            mismatches[f"{label}.collections"] = "approved collection membership is absent"
    if "tags" in expected:
        expected_tags = _tag_names(expected["tags"])
        actual_tags = _tag_names(actual.get("tags", ()))
        if not expected_tags.issubset(actual_tags):
            mismatches[f"{label}.tags"] = "approved tag is absent"
    if expected.get("itemType") == "note" and "data-codex-note=" in str(expected.get("note", "")):
        marker = re.search(r'data-codex-note="[^"]+"', str(expected["note"]))
        if marker is None or marker.group(0) not in str(actual.get("note", "")):
            mismatches[f"{label}.child_note"] = "approved Codex child-note marker is absent"
    if expected.get("itemType") == "attachment" and "path" in expected:
        if actual.get("path") != expected["path"]:
            mismatches[f"{label}.attachment_path"] = "approved linked attachment path differs"


def _tag_names(tags: Any) -> set[str]:
    if not isinstance(tags, (list, tuple)):
        return set()
    return {str(tag.get("tag", "")) if isinstance(tag, Mapping) else str(tag) for tag in tags}


def _object_data(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    data = value.get("data")
    return data if isinstance(data, Mapping) else value


def _object_version(value: Any) -> int | None:
    if not isinstance(value, Mapping):
        return None
    for source in (value, value.get("data", {})):
        if isinstance(source, Mapping) and isinstance(source.get("version"), int):
            return source["version"]
    return None


def _header_version(response: Any) -> int | None:
    headers = getattr(response, "headers", {})
    if not isinstance(headers, Mapping):
        return None
    for name, value in headers.items():
        if str(name).casefold() == "last-modified-version":
            try:
                return int(str(value))
            except ValueError:
                return None
    return None


def sanitize_tags(candidate: CandidateItem, profile_name: str) -> tuple[str, ...]:
    """Return profile-approved tags without elevating the source evidence."""
    _validate_profile(profile_name)
    if profile_name == _GENERIC_PROFILE:
        return ()
    cleaned: list[str] = []
    for tag in candidate.tags:
        normalized = str(tag).strip()
        if not normalized or not normalized.startswith(_ALLOWED_MICROWAVE_PREFIXES):
            continue
        if normalized.startswith("状态："):
            continue
        if candidate.evidence_level not in _FULL_TEXT_LEVELS and _contains_unverified_claim(normalized):
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    status = _canonical_status(candidate)
    if status:
        cleaned.append(status)
    return tuple(cleaned)


def render_note(candidate: CandidateItem, profile_name: str, report_path: str = "") -> str:
    """Render stable-marker Codex child-note HTML without unverified claims."""
    _validate_profile(profile_name)
    fields = _safe_note_fields(candidate)
    note_title = "微波光谱文献卡" if profile_name == _MICROWAVE_PROFILE else "文献卡"
    substitutions = {
        "NOTE_TITLE": note_title,
        "EVIDENCE_STATUS": _evidence_status(candidate),
        "RELEVANCE": _text(fields.get("relevance"), _bound_source_text(candidate.abstract, candidate.evidence_level), "未提供"),
        "EXPERIMENT": _text(fields.get("experiment"), _tags_with_prefix(candidate, "实验：", profile_name), "未提供"),
        "STRUCTURE": _text(fields.get("structure"), "未提供"),
        "THEORY_AND_ASSIGNMENT": _theory_and_assignment(fields),
        "USE_AND_LIMITS": _text(fields.get("use_and_limits"), "仅作为文献线索；请按证据状态使用。"),
        "REPORT_PATH": report_path or "未提供",
    }
    rendered = _TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, value in substitutions.items():
        rendered = rendered.replace("{{" + key + "}}", escape(str(value), quote=True))
    return rendered


def build_linked_attachment(parent_key: str, pdf_path: str | Path, allowed_roots: tuple[str | Path, ...]) -> dict[str, str]:
    """Build a linked-file attachment after strict final-D-drive validation."""
    if not parent_key:
        raise ValueError("parent_key is required")
    path = Path(pdf_path)
    if not path.is_absolute():
        raise ValueError("linked PDF path must be absolute")
    if path.suffix.casefold() != ".pdf":
        raise ValueError("linked attachment must be a PDF")
    try:
        resolved_path = path.resolve(strict=True)
        resolved_roots = tuple(Path(root).resolve(strict=True) for root in allowed_roots)
    except OSError as error:
        raise ValueError("linked PDF and approved roots must exist") from error
    if not resolved_path.is_file():
        raise ValueError("linked PDF path must be a file")
    if resolved_path.drive.casefold() != "d:":
        raise ValueError("linked PDF must be in a final D-drive directory")
    if any(part.casefold() in {"tmp", "temp", "cache", ".cache"} for part in resolved_path.parts):
        raise ValueError("linked PDF cannot be in a temporary or cache directory")
    if not resolved_roots or not any(resolved_path.is_relative_to(root) for root in resolved_roots):
        raise ValueError("linked PDF must be under an approved final directory")
    return {"itemType": "attachment", "parentItem": parent_key, "linkMode": "linked_file", "contentType": "application/pdf", "path": str(resolved_path), "title": resolved_path.name}


def candidate_to_zotero_item(candidate: CandidateItem, collection_key: str, profile_name: str) -> dict[str, Any]:
    """Build a bibliographic parent payload while leaving existing personal notes untouched."""
    _validate_profile(profile_name)
    if not collection_key:
        raise ValueError("collection_key is required")
    payload: dict[str, Any] = {
        "itemType": "journalArticle", "title": candidate.title,
        "creators": [dict(creator) for creator in candidate.creators], "date": candidate.year,
        "publicationTitle": candidate.publication_title, "DOI": candidate.doi,
        "url": candidate.url, "abstractNote": candidate.abstract, "language": candidate.language,
        "collections": [collection_key], "tags": [{"tag": tag} for tag in sanitize_tags(candidate, profile_name)],
    }
    identifiers = []
    if candidate.pmid:
        identifiers.append("PMID: " + candidate.pmid)
    if candidate.arxiv_id:
        identifiers.append("arXiv: " + candidate.arxiv_id)
    if identifiers:
        payload["extra"] = "\n".join(identifiers)
    return payload


def _validate_profile(profile_name: str) -> None:
    if profile_name not in {_GENERIC_PROFILE, _MICROWAVE_PROFILE}:
        raise ValueError("unknown Zotero profile")


def _has_accessible_pdf(pdf_path: str) -> bool:
    if not pdf_path:
        return False
    try:
        return Path(pdf_path).is_file()
    except OSError:
        return False


def _safe_note_fields(candidate: CandidateItem) -> Mapping[str, Any]:
    if candidate.evidence_level in _FULL_TEXT_LEVELS:
        return candidate.note_fields
    return {
        key: _bound_source_text(value, candidate.evidence_level)
        for key, value in candidate.note_fields.items()
        if key in _UNVERIFIED_SAFE_FIELDS
        and not any(token in str(key).casefold() for token in _UNVERIFIED_FIELD_TOKENS)
    }


def _evidence_status(candidate: CandidateItem) -> str:
    if candidate.evidence_level == EvidenceLevel.DEEP_READ:
        return "已深度精读"
    if candidate.evidence_level == EvidenceLevel.FULL_TEXT_VERIFIED:
        return "已核查全文"
    if candidate.evidence_level == EvidenceLevel.ABSTRACT_ONLY:
        return "基于摘要；状态：待获取全文"
    return "仅元数据；状态：待获取全文"


def _canonical_status(candidate: CandidateItem) -> str:
    if candidate.evidence_level == EvidenceLevel.DEEP_READ:
        return "状态：深度精读"
    if candidate.evidence_level == EvidenceLevel.FULL_TEXT_VERIFIED:
        return "状态：全文已核查"
    if not _has_accessible_pdf(candidate.linked_pdf):
        return "状态：待获取全文"
    return ""


def _tags_with_prefix(candidate: CandidateItem, prefix: str, profile_name: str) -> str:
    if profile_name != _MICROWAVE_PROFILE:
        return ""
    raw_tags = (str(tag) for tag in candidate.tags if str(tag).startswith(prefix))
    return "；".join(_bound_source_text(tag, candidate.evidence_level) for tag in raw_tags)


def _bound_source_text(value: Any, evidence_level: EvidenceLevel) -> str:
    """Suppress claims requiring full text from a metadata/abstract-only note."""
    text = str(value) if value is not None else ""
    if evidence_level in _FULL_TEXT_LEVELS or not text:
        return text
    if _contains_unverified_claim(text):
        return "待获取全文后补充"
    return text


def _contains_unverified_claim(text: str) -> bool:
    return bool(_UNVERIFIED_CLAIM.search(text))


def _theory_and_assignment(fields: Mapping[str, Any]) -> str:
    base = _text(fields.get("theory_and_assignment"), "未提供")
    extras = [str(value) for key, value in fields.items() if key not in {"theory_and_assignment", "relevance", "experiment", "structure", "use_and_limits"}]
    return "；".join([base, *extras]) if extras else base


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value)
    return ""
