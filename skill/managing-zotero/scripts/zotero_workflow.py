"""Pure payload builders for an evidence-bounded Zotero workflow."""

from __future__ import annotations

from html import escape
from pathlib import Path
import re
from typing import Any, Mapping

from zotero_models import CandidateItem, EvidenceLevel


_MICROWAVE_PROFILE = "microwave-spectroscopy"
_GENERIC_PROFILE = "generic"
_ALLOWED_MICROWAVE_PREFIXES = ("体系：", "实验：", "证据：", "计算：", "状态：")
_FULL_TEXT_LEVELS = frozenset((EvidenceLevel.FULL_TEXT_VERIFIED, EvidenceLevel.DEEP_READ))
_UNVERIFIED_FIELD_TOKENS = ("constant", "quotation", "quote", "page")
_UNVERIFIED_SAFE_FIELDS = frozenset(("relevance", "experiment", "structure", "theory_and_assignment", "use_and_limits"))
_UNVERIFIED_CLAIM = re.compile(
    r"(?:\b[A-Za-z][\w-]*\s*=\s*[+-]?\d+(?:[.,]\d+)?(?:\s*(?:GHz|MHz|kHz|Hz|cm-?1))?"
    r"|\b(?:p(?:age)?\.?\s*\d+|\d+\s*(?:页|pp?\.))"
    r"|[\"“”‘’])",
    re.IGNORECASE,
)
_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "assets" / "zotero-brief-note-template.html"


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
    if _UNVERIFIED_CLAIM.search(text):
        return "待获取全文后补充"
    return text


def _theory_and_assignment(fields: Mapping[str, Any]) -> str:
    base = _text(fields.get("theory_and_assignment"), "未提供")
    extras = [str(value) for key, value in fields.items() if key not in {"theory_and_assignment", "relevance", "experiment", "structure", "use_and_limits"}]
    return "；".join([base, *extras]) if extras else base


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value)
    return ""
