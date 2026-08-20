"""Append-only, redacted audit records for approved Zotero mutations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


_AUDIT_FILENAME = "managing-zotero-audit.jsonl"
_REDACTED = "[redacted]"
_SENSITIVE_FIELD = re.compile(r"(?:api[_-]?key|authorization|token|secret|zotero-api-key)", re.IGNORECASE)
_RAW_FIELD = re.compile(r"(?:^key$|header|payload|request|response|note|body)", re.IGNORECASE)
_INLINE_SECRET = re.compile(r"(?i)(?:bearer\s+|(?:api[_-]?key|authorization|token|secret)\s*[:=]\s*)[^\s,;]+")


@dataclass(frozen=True)
class AuditEvent:
    """A safe summary of one attempted approved mutation and its read-back."""

    plan_digest: str
    operation: str
    outcome: str
    target_collection: Mapping[str, str] = field(default_factory=dict)
    approved_action_count: int = 0
    approved_actions: tuple[str, ...] = ()
    successful_item_keys: tuple[str, ...] = ()
    unchanged_item_keys: tuple[str, ...] = ()
    failed: Mapping[str, str] = field(default_factory=dict)
    verified: bool | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


def redact(value: Any) -> object:
    """Recursively remove credentials, note bodies, request headers, and payloads."""
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            label = str(key)
            if label == "itemKey":
                result[label] = redact(child)
            elif _SENSITIVE_FIELD.search(label) or _RAW_FIELD.search(label):
                result[label] = _REDACTED
            else:
                result[label] = redact(child)
        return result
    if isinstance(value, (tuple, list)):
        return [redact(child) for child in value]
    if isinstance(value, str):
        return _INLINE_SECRET.sub(_REDACTED, value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _REDACTED


def append_audit_event(audit_dir: str | Path, event: AuditEvent) -> Path:
    """Durably append one canonical, redacted JSON object below an approved directory."""
    directory = Path(audit_dir)
    if not directory.is_absolute():
        raise ValueError("audit_dir must be absolute")
    approved_directory = directory.resolve(strict=False)
    audit_path = (approved_directory / _AUDIT_FILENAME).resolve(strict=False)
    try:
        audit_path.relative_to(approved_directory)
    except ValueError as exc:
        raise ValueError("audit path escapes the approved audit_dir") from exc
    approved_directory.mkdir(parents=True, exist_ok=True)
    record = redact(asdict(event))
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with audit_path.open("a", encoding="utf-8", newline="\n") as audit_file:
        audit_file.write(line + "\n")
        audit_file.flush()
        os.fsync(audit_file.fileno())
    return audit_path
