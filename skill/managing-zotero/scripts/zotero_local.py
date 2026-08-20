"""Approval-gated command line entry point for a loopback-only Zotero API."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
import json
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Callable, Mapping, TextIO

from zotero_client import (
    ZoteroAuthorizationError,
    ZoteroClient,
    ZoteroConnectionError,
    ZoteroLibraryLockedError,
    ZoteroVersionConflict,
)
from zotero_models import CandidateItem, EvidenceLevel, OperationKind, WriteAction, WritePlan, canonical_json, plan_digest
from zotero_workflow import (
    ApprovalProof, ApprovalRequired, BatchLimitExceeded, DuplicateConflict, PreviewStale,
    build_collection_plan, build_item_plan, build_linked_attachment,
    candidate_to_zotero_item, execute_plan, render_note,
)


_SECRET = re.compile(r"(?i)(?:api[_-]?key|authorization|token|secret|zotero-server-id)\s*[:=]\s*[^\s,;]+")
_ALLOWED_ACTIONS = frozenset(("create_collection", "upsert_items"))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="zotero_local.py", description="Structured local Zotero previews and approved writes.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="report local API capability")
    collections = commands.add_parser("collections", help="list collection metadata")
    collections.add_argument("--query", required=True)
    items = commands.add_parser("items", help="find candidate item matches")
    item_group = items.add_mutually_exclusive_group(required=True)
    item_group.add_argument("--doi")
    item_group.add_argument("--query")
    items.add_argument("--collection-key")
    preview_collection = commands.add_parser("preview-collection", help="write a collection creation preview")
    preview_collection.add_argument("--name", required=True)
    preview_collection.add_argument("--output", required=True)
    preview_items = commands.add_parser("preview-items", help="write an item-upsert preview")
    preview_items.add_argument("--input", required=True)
    preview_items.add_argument("--collection-key", required=True)
    preview_items.add_argument("--profile", choices=("generic", "microwave-spectroscopy"), required=True)
    preview_items.add_argument("--allowed-root", action="append", required=True)
    preview_items.add_argument("--output", required=True)
    apply = commands.add_parser("apply", help="apply one exact approved preview")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--approval-digest", required=True)
    apply.add_argument("--confirm-user-approved", action="store_true")
    apply.add_argument("--audit-dir", required=True)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[], Any] = ZoteroClient,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout, stderr = stdout or sys.stdout, stderr or sys.stderr
    try:
        with redirect_stdout(stdout):
            args = _parser().parse_args(argv)
        client = client_factory()
        result = _dispatch(args, client)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=stdout)
        return 0
    except SystemExit as exit_code:
        return int(exit_code.code)
    except (ValueError, TypeError, OSError, json.JSONDecodeError, ApprovalRequired, BatchLimitExceeded) as error:
        return _error(stderr, 2, error)
    except (ZoteroConnectionError, ZoteroLibraryLockedError) as error:
        return _error(stderr, 3, error)
    except ZoteroAuthorizationError as error:
        return _error(stderr, 4, error)
    except (PreviewStale, ZoteroVersionConflict) as error:
        return _error(stderr, 5, error)
    except _PartialResult as error:
        print(json.dumps(error.result, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=stdout)
        return 6
    except Exception as error:
        return _error(stderr, 6, error)


def _dispatch(args: argparse.Namespace, client: Any) -> dict[str, Any]:
    if args.command == "status":
        status = client.probe()
        return {
            "connection": "connected" if status.connected else "unavailable",
            "api_version": status.api_version,
            "schema_version": status.schema_version,
            "server_id": _mask_server_id(status.server_id),
            "read_only": not status.write_candidate,
            "write_candidate": bool(status.write_candidate),
            "reason": status.reason,
        }
    if args.command == "collections":
        payload, _ = client.get_json("users/0/collections", {"q": args.query})
        return {"collections": _collection_metadata(payload)}
    if args.command == "items":
        query = {"doi": args.doi} if args.doi else {"q": args.query, "collectionKey": args.collection_key or ""}
        payload, _ = client.get_json("users/0/items", query)
        return {"items": _item_metadata(payload)}
    if args.command == "preview-collection":
        status = _preview_status(client)
        _, response = client.get_json("users/0/collections")
        plan = build_collection_plan(args.name, _header_version(response), server_id=status.server_id)
        _write_plan(args.output, plan)
        return {"plan": str(Path(args.output)), "digest": plan_digest(plan), "operation": plan.operation.value}
    if args.command == "preview-items":
        status = _preview_status(client)
        candidates = _read_candidates(args.input)
        if len(candidates) > 10:
            raise BatchLimitExceeded("candidate file may contain at most 10 papers")
        roots = _validate_allowed_roots(args.allowed_root, candidates)
        collection = _read_collection(client, args.collection_key)
        existing = _library_matches(client, candidates)
        plan = build_item_plan(candidates, collection, existing, server_id=status.server_id)
        plan = _with_profile(plan, candidates, args.profile, roots)
        _write_plan(args.output, plan)
        return {"plan": str(Path(args.output)), "digest": plan_digest(plan), "operation": plan.operation.value}
    if args.command == "apply":
        _validate_digest(args.approval_digest)
        if not args.confirm_user_approved:
            raise ApprovalRequired("--confirm-user-approved is required")
        audit_dir = Path(args.audit_dir)
        if not audit_dir.is_absolute():
            raise ValueError("audit-dir must be absolute")
        plan = _read_plan(args.plan)
        status = client.probe()
        if not status.connected or not status.write_candidate:
            raise ZoteroConnectionError("local Zotero API is read-only or unavailable")
        execution = execute_plan(client, plan, ApprovalProof(args.approval_digest, True), audit_dir=audit_dir)
        result = asdict(execution)
        if not execution.verified or execution.failed:
            raise _PartialResult(result)
        return result
    raise ValueError("unknown command")


class _PartialResult(RuntimeError):
    def __init__(self, result: Mapping[str, Any]):
        super().__init__("partial Zotero write")
        self.result = dict(result)


def _error(stderr: TextIO, code: int, error: Exception) -> int:
    if isinstance(error, _PartialResult):
        code = 6
        message = "write completed with a partial result or read-back mismatch"
    else:
        message = _SECRET.sub("[REDACTED]", str(error))
    print(f"error: {message}", file=stderr)
    return code


def _mask_server_id(value: str) -> str:
    return "" if not value else "[REDACTED]"


def _header_version(response: Any) -> int:
    headers = getattr(response, "headers", {})
    for name, value in getattr(headers, "items", lambda: ())():
        if str(name).casefold() == "last-modified-version":
            try:
                return int(value)
            except (TypeError, ValueError):
                break
    raise ValueError("local API response did not provide a library version")


def _collection_metadata(payload: Any) -> list[dict[str, Any]]:
    return [{key: data[key] for key in ("key", "name", "version", "parentCollection") if key in data} for data in _as_list(payload) if isinstance(data, Mapping)]


def _item_metadata(payload: Any) -> list[dict[str, Any]]:
    output = []
    for item in _as_list(payload):
        data = item.get("data", item) if isinstance(item, Mapping) else {}
        if isinstance(data, Mapping):
            output.append({key: data[key] for key in ("key", "title", "DOI", "date", "itemType") if key in data})
    return output


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _read_collection(client: Any, key: str) -> Mapping[str, Any]:
    payload, _ = client.get_json(f"users/0/collections/{key}")
    if not isinstance(payload, Mapping):
        raise ValueError("collection was not returned by local Zotero")
    data = payload.get("data", payload)
    if not isinstance(data, Mapping) or str(data.get("key", key)) != key:
        raise ValueError("collection key does not match the preview target")
    return {**data, "key": key}


def _preview_status(client: Any) -> Any:
    status = client.probe()
    if not status.connected or not status.server_id:
        raise ZoteroConnectionError("local Zotero Server ID is unavailable")
    return status


def _library_matches(client: Any, candidates: tuple[CandidateItem, ...]) -> list[Mapping[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        if candidate.doi:
            query = {"doi": candidate.doi}
        elif candidate.pmid:
            query = {"q": "PMID: " + candidate.pmid}
        elif candidate.arxiv_id:
            query = {"q": "arXiv: " + candidate.arxiv_id}
        else:
            query = {"q": candidate.title}
        payload, _ = client.get_json("users/0/items", query)
        for item in _as_list(payload):
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("key", ""))
            if key:
                found[key] = item
    return list(found.values())


def _new_key() -> str:
    alphabet = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _read_candidates(path: str) -> tuple[CandidateItem, ...]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    records = value.get("candidates") if isinstance(value, Mapping) else value
    if not isinstance(records, list):
        raise ValueError("candidate input must be a JSON list or candidates object")
    return tuple(_candidate(record) for record in records)


def _validate_allowed_roots(values: list[str], candidates: tuple[CandidateItem, ...]) -> tuple[Path, ...]:
    roots = tuple(Path(value).resolve(strict=True) for value in values)
    if not roots or not all(root.is_dir() for root in roots):
        raise ValueError("allowed-root values must be existing directories")
    for candidate in candidates:
        if not candidate.linked_pdf:
            continue
        pdf = Path(candidate.linked_pdf).resolve(strict=True)
        if not pdf.is_file() or pdf.suffix.casefold() != ".pdf" or not any(pdf.is_relative_to(root) for root in roots):
            raise ValueError("linked PDF must be an existing PDF under an allowed-root")
    return roots


def _with_profile(
    plan: WritePlan,
    candidates: tuple[CandidateItem, ...],
    profile: str,
    allowed_roots: tuple[Path, ...],
) -> WritePlan:
    payloads = plan.actions[0].payload
    if not isinstance(payloads, (list, tuple)):
        return plan
    revised: list[Mapping[str, Any]] = []
    for candidate, payload in zip(candidates, payloads):
        if not isinstance(payload, Mapping):
            raise ValueError("item payload is malformed")
        if isinstance(payload.get("version"), int) and payload.get("version", 0) > 0:
            parent = dict(payload)
        else:
            parent = candidate_to_zotero_item(candidate, plan.collection_key, profile)
            parent["key"] = str(payload.get("key") or _new_key())
            parent["version"] = 0
        revised.append(parent)
        parent_key = str(parent["key"])
        revised.append({
            "itemType": "note",
            "key": _new_key(),
            "version": 0,
            "parentItem": parent_key,
            "note": render_note(candidate, profile),
        })
        if candidate.linked_pdf:
            attachment = build_linked_attachment(parent_key, candidate.linked_pdf, allowed_roots)
            attachment["key"] = _new_key()
            attachment["version"] = 0
            revised.append(attachment)
    return WritePlan(
        plan.operation,
        plan.collection_key,
        plan.collection_name,
        (WriteAction("upsert_items", tuple(revised)),),
        plan.expected_versions,
        plan.library_version,
        plan.server_fingerprint,
        plan.duplicate_checks,
        tuple(str(root) for root in allowed_roots),
    )


def _candidate(value: Any) -> CandidateItem:
    if not isinstance(value, Mapping) or not isinstance(value.get("title"), str) or not value["title"].strip():
        raise ValueError("each candidate requires a title")
    level = value.get("evidence_level", EvidenceLevel.METADATA_ONLY.value)
    try:
        evidence_level = EvidenceLevel(level)
    except ValueError as exc:
        raise ValueError("candidate evidence_level is invalid") from exc
    creators = value.get("creators", ())
    tags = value.get("tags", ())
    if not isinstance(creators, list) or not all(isinstance(creator, Mapping) for creator in creators) or not isinstance(tags, list):
        raise ValueError("candidate creators and tags must be lists")
    return CandidateItem(
        title=value["title"], creators=tuple(dict(creator) for creator in creators),
        year=str(value.get("year", "")), publication_title=str(value.get("publication_title", "")),
        doi=str(value.get("doi", "")), pmid=str(value.get("pmid", "")), arxiv_id=str(value.get("arxiv_id", "")),
        url=str(value.get("url", "")), abstract=str(value.get("abstract", "")), language=str(value.get("language", "")),
        evidence_level=evidence_level, tags=tuple(str(tag) for tag in tags), linked_pdf=str(value.get("linked_pdf", "")),
        note_fields=dict(value.get("note_fields", {})) if isinstance(value.get("note_fields", {}), Mapping) else {},
    )


def _write_plan(path: str, plan: WritePlan) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_json(plan) + "\n", encoding="utf-8")


def _read_plan(path: str) -> WritePlan:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("plan must be a JSON object")
    try:
        operation = OperationKind(value["operation"])
        actions = tuple(WriteAction(str(action["kind"]), action["payload"], str(action.get("item_key", ""))) for action in value["actions"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("plan is malformed") from exc
    if any(action.kind not in _ALLOWED_ACTIONS for action in actions):
        raise ValueError("plan contains an unknown action kind")
    expected_versions = value.get("expected_versions", {})
    if not isinstance(expected_versions, Mapping) or not all(isinstance(version, int) for version in expected_versions.values()):
        raise ValueError("plan expected_versions is invalid")
    version = value.get("library_version")
    if version is not None and not isinstance(version, int):
        raise ValueError("plan library_version is invalid")
    fingerprint = value.get("server_fingerprint", "")
    duplicate_checks = value.get("duplicate_checks", [])
    allowed_roots = value.get("allowed_roots", [])
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("plan Server-ID fingerprint is missing or invalid")
    if not isinstance(duplicate_checks, list) or not all(isinstance(check, Mapping) for check in duplicate_checks):
        raise ValueError("plan duplicate bindings are invalid")
    if not isinstance(allowed_roots, list) or not all(isinstance(root, str) for root in allowed_roots):
        raise ValueError("plan allowed roots are invalid")
    return WritePlan(
        operation,
        str(value.get("collection_key", "")),
        str(value.get("collection_name", "")),
        actions,
        dict(expected_versions),
        version,
        fingerprint,
        tuple(dict(check) for check in duplicate_checks),
        tuple(allowed_roots),
    )


def _validate_digest(value: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("approval-digest must be a 64-character hexadecimal digest")


if __name__ == "__main__":
    raise SystemExit(main())
