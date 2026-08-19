from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from zotero_models import CandidateItem, DuplicateMatch, MatchKind


_DOI_PREFIX = re.compile(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)
_PMID_PREFIX = re.compile(r"^pmid\s*:\s*", re.IGNORECASE)
_ARXIV_PREFIX = re.compile(r"^(?:arxiv\s*:\s*|https?://arxiv\.org/(?:abs|pdf)/)", re.IGNORECASE)
_PMID_IN_EXTRA = re.compile(r"(?:^|\n)\s*pmid\s*:\s*([^\s;,]+)", re.IGNORECASE)
_ARXIV_IN_EXTRA = re.compile(r"(?:^|\n)\s*arxiv\s*:\s*([^\s;,]+)", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def normalize_doi(value: str) -> str:
    """Return a comparison-only DOI representation."""
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    normalized = _DOI_PREFIX.sub("", normalized)
    return normalized.strip(" \t\r\n.,;:<>[]{}\"").casefold()


def normalize_external_id(value: str) -> str:
    """Return a comparison-only PMID or arXiv identifier representation."""
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    normalized = _PMID_PREFIX.sub("", normalized)
    normalized = _ARXIV_PREFIX.sub("", normalized)
    return normalized.strip(" \t\r\n.,;:<>[]{}\"").casefold()


def normalize_title(value: str) -> str:
    """Return a comparison-only title representation."""
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = "".join("-" if unicodedata.category(char) == "Pd" else char for char in normalized)
    normalized = " ".join(normalized.split()).casefold()
    return _strip_surrounding_punctuation(normalized)


def first_author_signature(creators: Iterable[Mapping[str, Any]]) -> str:
    """Return the normalized family-name signature of the first creator."""
    for creator in creators or ():
        if not isinstance(creator, Mapping):
            continue
        name = creator.get("lastName") or creator.get("name") or ""
        if name:
            return normalize_title(str(name))
    return ""


def classify_duplicate(candidate: CandidateItem, zotero_items: Iterable[Mapping[str, Any]]) -> DuplicateMatch:
    """Classify a candidate without changing, merging, or selecting bibliographic conflicts."""
    items = tuple(_item_data(item) for item in zotero_items)

    for kind, identifier in (
        ("doi", normalize_doi(candidate.doi)),
        ("pmid", normalize_external_id(candidate.pmid)),
        ("arxiv", normalize_external_id(candidate.arxiv_id)),
    ):
        if not identifier:
            continue
        for item_key, data in items:
            if identifier in _identifiers_for(kind, data):
                return DuplicateMatch(MatchKind.EXACT_IDENTIFIER, item_key, (kind,))

    candidate_signature = _bibliographic_signature(candidate.title, candidate.creators, candidate.year)
    if not candidate_signature:
        return DuplicateMatch(MatchKind.NONE)

    matches = [
        item_key
        for item_key, data in items
        if _bibliographic_signature(
            str(data.get("title", "")),
            data.get("creators", ()),
            str(data.get("date", "")),
        ) == candidate_signature
    ]
    if len(matches) == 1:
        return DuplicateMatch(MatchKind.PROBABLE_BIBLIOGRAPHIC, matches[0], ("title_author_year",))
    if len(matches) > 1:
        return DuplicateMatch(MatchKind.PROBABLE_BIBLIOGRAPHIC, reasons=("title_author_year", "ambiguous"))
    return DuplicateMatch(MatchKind.NONE)


def _item_data(item: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(item, Mapping):
        return "", {}
    data = item.get("data", {})
    return str(item.get("key", "")), data if isinstance(data, Mapping) else {}


def _identifiers_for(kind: str, data: Mapping[str, Any]) -> tuple[str, ...]:
    if kind == "doi":
        value = data.get("DOI", data.get("doi", ""))
        return (normalize_doi(str(value)),) if value else ()

    extra = str(data.get("extra", ""))
    pattern = _PMID_IN_EXTRA if kind == "pmid" else _ARXIV_IN_EXTRA
    normalizer = normalize_external_id
    return tuple(normalizer(match.group(1)) for match in pattern.finditer(extra))


def _bibliographic_signature(title: str, creators: Any, year: str) -> tuple[str, str, str] | None:
    normalized_title = normalize_title(title)
    author = first_author_signature(creators if isinstance(creators, Iterable) else ())
    year_match = _YEAR.search(year)
    if not normalized_title or not author or not year_match:
        return None
    return normalized_title, author, year_match.group(1)


def _strip_surrounding_punctuation(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and unicodedata.category(value[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(value[end - 1]).startswith("P"):
        end -= 1
    return value[start:end].strip()
