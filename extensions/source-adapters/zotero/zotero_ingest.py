#!/usr/bin/env python3
"""Fetch Zotero source data and render Things v2 research-info candidates.

This script intentionally does not promote records into ``site-specific/metadata/records``
or call the validator. Its transform output is review evidence that must be
promoted explicitly after validation.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen


ZOTERO_API_VERSION = "3"
PAGE_SIZE = 100
MAX_HTTP_ATTEMPTS = 5
MAX_SNAPSHOT_ATTEMPTS = 3
TARGET_CLASSES = (
    "XYZDataset",
    "XYZDocument",
    "XYZInstrument",
    "XYZPublication",
    "XYZPublicationVenue",
)

PUBLICATION_KINDS = {
    "book": "bibo:Book",
    "bookSection": "bibo:BookSection",
    "conferencePaper": "bibo:Article",
    "document": "bibo:Document",
    "journalArticle": "bibo:AcademicArticle",
    "preprint": "bibo:Manuscript",
    "presentation": "bibo:Slideshow",
    "report": "bibo:Report",
    "thesis": "bibo:Thesis",
    "webpage": "bibo:Webpage",
}

DOCUMENT_COLLECTION_KINDS = frozenset(
    {"dataset", "instrument", "publication", "registry"}
)

CREATOR_ROLES = {
    "artist": "marcrel:art",
    "author": "marcrel:aut",
    "bookAuthor": "marcrel:aut",
    "contributor": "marcrel:ctb",
    "editor": "marcrel:edt",
    "performer": "marcrel:prf",
    "presenter": "marcrel:pre",
    "programmer": "marcrel:prg",
    "recipient": "marcrel:rcp",
    "seriesEditor": "marcrel:edt",
    "sponsor": "marcrel:spn",
    "translator": "marcrel:trl",
}

DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
DOI_IN_TEXT_RE = re.compile(r"(?i)(?:doi:\s*|https?://doi\.org/)(10\.\d{4,9}/\S+)")
ISSN_RE = re.compile(r"\b\d{4}-\d{3}[\dXx]\b")
PMID_RE = re.compile(r"(?im)^PMID:\s*(\S+)\s*$")
PMCID_RE = re.compile(r"(?im)^PMCID:\s*(\S+)\s*$")
FULL_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\b|T)")


@dataclass(frozen=True)
class SourceItem:
    item: dict[str, Any]
    collections: tuple[str, ...]
    doi: str | None
    selected: bool

    @property
    def data(self) -> dict[str, Any]:
        value = self.item.get("data", self.item)
        return value if isinstance(value, dict) else {}

    @property
    def key(self) -> str:
        return str(self.data.get("key") or self.item.get("key") or "")


class SnapshotChanged(RuntimeError):
    """Signal that Zotero changed while a multi-page snapshot was read."""


@dataclass(frozen=True)
class PaginatedResult:
    """One complete, version-consistent Zotero multi-object response."""

    records: tuple[dict[str, Any], ...]
    library_version: int
    response_api_version: str
    total_results: int
    urls: tuple[str, ...]


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def retry_delay(headers: Any, attempt: int) -> float:
    """Return Zotero's requested delay or a bounded exponential fallback."""
    for name in ("Retry-After", "Backoff"):
        value = headers.get(name) if headers is not None else None
        if value is not None:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                break
    return float(min(2**attempt, 16))


def fetch_json(url: str, user_agent: str) -> tuple[Any, dict[str, str]]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
            "Zotero-API-Version": ZOTERO_API_VERSION,
        },
    )
    for attempt in range(MAX_HTTP_ATTEMPTS):
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.load(response)
                headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                response_version = headers.get("zotero-api-version")
                if response_version != ZOTERO_API_VERSION:
                    raise ValueError(
                        "Zotero returned API version "
                        f"{response_version!r}; expected {ZOTERO_API_VERSION!r}"
                    )
                backoff = headers.get("backoff")
                if backoff:
                    time.sleep(retry_delay(response.headers, attempt))
                return payload, headers
        except HTTPError as error:
            if error.code not in {429, 503} or attempt == MAX_HTTP_ATTEMPTS - 1:
                raise
            time.sleep(retry_delay(error.headers, attempt))
    raise RuntimeError(f"Unable to fetch {url}")


def required_integer_header(headers: dict[str, str], name: str, url: str) -> int:
    value = headers.get(name)
    if value is None:
        raise ValueError(f"Zotero response for {url} is missing {name}")
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(
            f"Zotero response for {url} has invalid {name}: {value!r}"
        ) from error


def fetch_paginated(api_root: str, endpoint: str, user_agent: str) -> PaginatedResult:
    records: list[dict[str, Any]] = []
    urls: list[str] = []
    library_version: int | None = None
    response_api_version: str | None = None
    total_results: int | None = None
    start = 0

    while True:
        query = urlencode(
            {
                "format": "json",
                "include": "data",
                "limit": PAGE_SIZE,
                "start": start,
            }
        )
        url = f"{api_root}/{endpoint}?{query}"
        payload, headers = fetch_json(url, user_agent)
        if not isinstance(payload, list):
            raise ValueError(f"Expected a list from {url}")
        if not all(isinstance(record, dict) for record in payload):
            raise ValueError(f"Zotero response for {url} contains a non-object record")

        page_version = required_integer_header(headers, "last-modified-version", url)
        page_total = required_integer_header(headers, "total-results", url)
        page_api_version = headers["zotero-api-version"]
        if library_version is None:
            library_version = page_version
            total_results = page_total
            response_api_version = page_api_version
        elif page_version != library_version or page_total != total_results:
            raise SnapshotChanged(
                f"Zotero {endpoint} changed during pagination: "
                f"version {library_version}/{page_version}, "
                f"total {total_results}/{page_total}"
            )

        records.extend(payload)
        urls.append(url)
        if not payload or len(records) >= page_total:
            break
        start += len(payload)

    if library_version is None or total_results is None or response_api_version is None:
        raise ValueError(f"Zotero returned no response metadata for {endpoint}")
    if len(records) != total_results:
        raise SnapshotChanged(
            f"Zotero {endpoint} returned {len(records)} records but declared "
            f"{total_results}"
        )
    keys = [record_key(record) for record in records]
    missing = sum(not key for key in keys)
    duplicates = sorted(
        key for key, count in Counter(keys).items() if key and count > 1
    )
    if missing or duplicates:
        raise SnapshotChanged(
            f"Zotero {endpoint} pagination is not a unique key set: "
            f"missing={missing}, duplicates={duplicates}"
        )
    return PaginatedResult(
        records=tuple(records),
        library_version=library_version,
        response_api_version=response_api_version,
        total_results=total_results,
        urls=tuple(urls),
    )


def source_content_sha256(
    collections: list[dict[str, Any]], items: list[dict[str, Any]]
) -> str:
    """Digest normalized snapshot content independently of retrieval time."""
    payload = json.dumps(
        {"collections": collections, "items": items},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate the acquisition provenance and normalized source payload."""
    source = snapshot.get("source")
    collections = snapshot.get("collections")
    items = snapshot.get("items")
    if not isinstance(source, dict):
        raise ValueError("Zotero snapshot source provenance must be a mapping")
    if not isinstance(collections, list) or not isinstance(items, list):
        raise ValueError("Zotero snapshot collections and items must be lists")
    if not all(isinstance(record, dict) for record in [*collections, *items]):
        raise ValueError("Zotero snapshot contains a non-object record")

    group_id = source.get("group_id")
    library_version = source.get("library_version")
    if not isinstance(group_id, int) or group_id <= 0:
        raise ValueError("Zotero snapshot group_id must be a positive integer")
    if not isinstance(library_version, int) or library_version < 0:
        raise ValueError(
            "Zotero snapshot library_version must be a nonnegative integer"
        )
    expected_root = f"https://api.zotero.org/groups/{group_id}"
    if source.get("api_root") != expected_root:
        raise ValueError("Zotero snapshot api_root does not match its group_id")
    if source.get("zotero_api_version") != ZOTERO_API_VERSION:
        raise ValueError("Zotero snapshot does not pin API version 3")
    if source.get("response_api_version") != ZOTERO_API_VERSION:
        raise ValueError(
            "Zotero response API version does not match the requested version"
        )
    for name, records in (("collections", collections), ("top_level_items", items)):
        if source.get(f"total_{name}") != len(records):
            raise ValueError(f"Zotero snapshot total_{name} does not match its payload")
        keys = [record_key(record) for record in records]
        if not all(keys) or len(keys) != len(set(keys)):
            raise ValueError(f"Zotero snapshot {name} keys are missing or duplicated")
        if records != sorted(records, key=record_key):
            raise ValueError(f"Zotero snapshot {name} are not deterministically sorted")
    if source.get("content_sha256") != source_content_sha256(collections, items):
        raise ValueError("Zotero snapshot content_sha256 does not match its payload")
    try:
        fetched_at = datetime.fromisoformat(str(source.get("fetched_at")))
    except ValueError as error:
        raise ValueError("Zotero snapshot fetched_at is not ISO 8601") from error
    if fetched_at.tzinfo is None:
        raise ValueError("Zotero snapshot fetched_at must include a timezone")
    for key, endpoint in (
        ("collection_urls", "collections"),
        ("item_urls", "items/top"),
    ):
        urls = source.get(key)
        if not isinstance(urls, list) or not urls:
            raise ValueError(f"Zotero snapshot {key} must be a non-empty list")
        expected_prefix = f"{expected_root}/{endpoint}?"
        if not all(
            isinstance(url, str) and url.startswith(expected_prefix) for url in urls
        ):
            raise ValueError(f"Zotero snapshot {key} contains an unexpected URL")
    return source


def command_fetch(args: argparse.Namespace) -> None:
    api_root = f"https://api.zotero.org/groups/{args.group_id}"
    snapshot_attempts = getattr(args, "snapshot_attempts", MAX_SNAPSHOT_ATTEMPTS)
    if snapshot_attempts < 1:
        raise ValueError("--snapshot-attempts must be at least 1")
    for attempt in range(snapshot_attempts):
        try:
            collection_result = fetch_paginated(
                api_root, "collections", args.user_agent
            )
            item_result = fetch_paginated(api_root, "items/top", args.user_agent)
            if collection_result.library_version != item_result.library_version:
                raise SnapshotChanged(
                    "Zotero changed between collection and item reads: "
                    f"{collection_result.library_version} != "
                    f"{item_result.library_version}"
                )
            break
        except SnapshotChanged:
            if attempt == snapshot_attempts - 1:
                raise
    library_version = collection_result.library_version
    expected_version = getattr(args, "expected_library_version", None)
    if expected_version is not None and library_version != expected_version:
        raise SnapshotChanged(
            f"Zotero library version is {library_version}, expected "
            f"{expected_version}"
        )
    collections = sorted(collection_result.records, key=record_key)
    items = sorted(item_result.records, key=record_key)
    snapshot = {
        "source": {
            "api_root": api_root,
            "collection_urls": list(collection_result.urls),
            "content_sha256": source_content_sha256(collections, items),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "group_id": args.group_id,
            "item_urls": list(item_result.urls),
            "library_version": library_version,
            "response_api_version": item_result.response_api_version,
            "snapshot_attempt": attempt + 1,
            "total_collections": collection_result.total_results,
            "total_top_level_items": item_result.total_results,
            "zotero_api_version": ZOTERO_API_VERSION,
        },
        "collections": collections,
        "items": items,
    }
    atomic_write_json(args.output, snapshot)
    print(
        f"Fetched {len(collections)} collections and {len(items)} top-level items "
        f"at library version {snapshot['source']['library_version']}."
    )


def record_key(record: dict[str, Any]) -> str:
    data = record.get("data", record)
    return str(data.get("key", "")) if isinstance(data, dict) else ""


def clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalize_doi(value: Any) -> str | None:
    text = unquote(clean_text(value))
    text = DOI_PREFIX_RE.sub("", text).strip().rstrip(".,;)")
    if not text.lower().startswith("10.") or "/" not in text:
        return None
    return text.lower()


def item_doi(data: dict[str, Any]) -> str | None:
    if doi := normalize_doi(data.get("DOI")):
        return doi
    match = DOI_IN_TEXT_RE.search(clean_text(data.get("extra")))
    return normalize_doi(match.group(1)) if match else None


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", clean_text(value)).casefold()
    return "".join(character for character in text if character.isalnum())


def normalize_collection_name(value: Any) -> str:
    """Normalize reviewed collection labels without changing their identity."""
    name = " ".join(clean_text(value).casefold().split())
    return name.removeprefix("con ")


def creator_name(creator: dict[str, Any]) -> str:
    if name := clean_text(creator.get("name")):
        return name
    return " ".join(
        part
        for part in (
            clean_text(creator.get("firstName")),
            clean_text(creator.get("lastName")),
        )
        if part
    )


def load_entity_index(paths: Iterable[Path]) -> dict[str, str]:
    candidates: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as stream:
            records = json.load(stream)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or not record.get("pid"):
                continue
            names = {
                clean_text(record.get("display_label")),
                clean_text(record.get("formatted_name")),
                clean_text(record.get("name")),
                clean_text(record.get("title")),
            }
            parts = [
                clean_text(record.get("given_name")),
                *[
                    clean_text(value)
                    for value in (record.get("additional_names") or [])
                ],
                clean_text(record.get("family_name")),
            ]
            names.add(" ".join(part for part in parts if part))
            for name in names:
                if normalized := normalize_name(name):
                    candidates[normalized].add(str(record["pid"]))
    return {
        name: next(iter(pids)) for name, pids in candidates.items() if len(pids) == 1
    }


def load_creator_mappings(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}

    import yaml

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("format_version") != 1:
        raise ValueError("Creator mapping must be a mapping with format_version: 1")

    mappings: dict[str, str] = {}
    for group in document.get("mappings", []):
        if not isinstance(group, dict) or not clean_text(group.get("pid")):
            raise ValueError("Every creator mapping group must define a pid")
        pid = clean_text(group["pid"])
        for alias in group.get("aliases", []):
            normalized = normalize_name(alias)
            if not normalized:
                raise ValueError(f"Creator mapping contains an empty alias for {pid}")
            if normalized in mappings and mappings[normalized] != pid:
                raise ValueError(f"Creator alias maps to multiple PIDs: {alias}")
            mappings[normalized] = pid
    return mappings


def collection_names(snapshot: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for collection in snapshot.get("collections", []):
        data = collection.get("data", collection)
        if not isinstance(data, dict):
            continue
        key = clean_text(data.get("key") or collection.get("key"))
        name = clean_text(data.get("name"))
        if key and name:
            names[key] = name
    return names


def source_items(
    snapshot: dict[str, Any], included_collections: set[str]
) -> list[SourceItem]:
    names = collection_names(snapshot)
    result: list[SourceItem] = []
    for item in snapshot.get("items", []):
        if not isinstance(item, dict):
            continue
        data = item.get("data", item)
        if not isinstance(data, dict) or data.get("deleted"):
            continue
        item_collections = tuple(
            sorted(
                {
                    names.get(clean_text(key), f"[unknown:{clean_text(key)}]")
                    for key in data.get("collections", [])
                    if clean_text(key)
                }
            )
        )
        selected = any(
            normalize_collection_name(name) in included_collections
            for name in item_collections
        )
        result.append(
            SourceItem(
                item=item,
                collections=item_collections,
                doi=item_doi(data),
                selected=selected,
            )
        )
    return result


def classify(
    item: SourceItem,
    document_collection_classes: dict[str, str],
) -> tuple[str | None, str | None, str | None]:
    item_type = clean_text(item.data.get("itemType"))
    collection_set = {normalize_collection_name(name) for name in item.collections}

    if item_type == "dataset":
        return "XYZDataset", None, None
    if item_type == "computerProgram":
        return "XYZInstrument", "obo:IAO_0000010", None
    if item_type in PUBLICATION_KINDS and item_type != "document":
        return "XYZPublication", PUBLICATION_KINDS[item_type], None
    if item_type == "document":
        hints = {
            document_collection_classes[collection]
            for collection in collection_set
            if collection in document_collection_classes
        }
        if len(hints) > 1:
            return None, None, "conflicting collection classification"
        if hints == {"dataset"}:
            return "XYZDataset", None, None
        if hints == {"instrument"}:
            return "XYZInstrument", "obo:IAO_0000010", None
        if hints == {"publication"}:
            return "XYZPublication", "bibo:Document", None
        if hints == {"registry"}:
            return None, None, "registry enrichment required"
        return "XYZDocument", "bibo:Document", None
    return None, None, f"unmapped Zotero item type: {item_type or '[missing]'}"


def completeness(item: SourceItem) -> tuple[int, int, str]:
    data = item.data
    fields = (
        "title",
        "abstractNote",
        "DOI",
        "url",
        "date",
        "publicationTitle",
        "ISSN",
        "ISBN",
        "language",
        "rights",
    )
    score = sum(bool(clean_text(data.get(field))) for field in fields)
    score += min(len(data.get("creators", [])), 10)
    score += 100 if data.get("itemType") != "document" else 0
    version = int(data.get("version") or item.item.get("version") or 0)
    return score, version, item.key


def extract_issns(value: Any) -> list[str]:
    return sorted({match.upper() for match in ISSN_RE.findall(clean_text(value))})


def extract_isbns(value: Any) -> list[str]:
    candidates = re.findall(
        r"(?<!\d)(?:97[89][\d -]{10,16}|[\dXx][\dXx -]{8,15})(?!\d)",
        clean_text(value),
    )
    normalized = {re.sub(r"[^\dXx]", "", candidate).upper() for candidate in candidates}
    return sorted(code for code in normalized if len(code) in {10, 13})


def identifiers_for(
    group: list[SourceItem], doi: str | None, group_id: int
) -> list[dict[str, str]]:
    identifiers: set[tuple[str, str]] = set()
    if doi:
        identifiers.add(("dlthings:DOI", doi))
    for item in group:
        data = item.data
        if item.key:
            identifiers.add(
                ("dlthings:Identifier", f"zotero:group:{group_id}:item:{item.key}")
            )
        if url := clean_text(data.get("url")):
            identifiers.add(("dlthings:Identifier", url))
        for issn in extract_issns(data.get("ISSN")):
            identifiers.add(("dlthings:ISSN", issn))
        for isbn in extract_isbns(data.get("ISBN")):
            identifiers.add(("dlthings:Identifier", f"ISBN:{isbn}"))
        extra = clean_text(data.get("extra"))
        for pattern, prefix in ((PMID_RE, "PMID"), (PMCID_RE, "PMCID")):
            if match := pattern.search(extra):
                identifiers.add(("dlthings:Identifier", f"{prefix}:{match.group(1)}"))
    return [
        {"notation": notation, "schema_type": schema_type}
        for schema_type, notation in sorted(identifiers)
    ]


def canonical_pid(item: SourceItem, doi: str | None, group_id: int) -> str:
    if doi:
        return f"https://doi.org/{doi}"
    return f"https://api.zotero.org/groups/{group_id}/items/{item.key}"


def creator_records(item: SourceItem) -> list[dict[str, Any]]:
    creators = item.data.get("creators", [])
    if not isinstance(creators, list):
        raise ValueError(f"{item.key}: Zotero creators must be a list")
    if not all(isinstance(creator, dict) for creator in creators):
        raise ValueError(f"{item.key}: Zotero creators must be mappings")
    return creators


def resolve_creators(
    item: SourceItem,
    people: dict[str, str],
    organizations: dict[str, str],
    creator_mappings: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    attributions: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for creator in creator_records(item):
        creator_type = clean_text(creator.get("creatorType"))
        if creator_type not in CREATOR_ROLES:
            raise ValueError(
                f"{item.key}: unsupported Zotero creator role "
                f"{creator_type or '<missing>'!r}"
            )
        name = creator_name(creator)
        normalized = normalize_name(name)
        if not normalized:
            continue
        if normalized in creator_mappings:
            matches = {creator_mappings[normalized]}
        elif creator.get("name"):
            matches = {
                value
                for value in (organizations.get(normalized), people.get(normalized))
                if value
            }
        else:
            matches = {value for value in (people.get(normalized),) if value}
        role = CREATOR_ROLES[creator_type]
        if len(matches) != 1:
            unresolved.append(
                {
                    "creator_type": creator_type,
                    "item_key": item.key,
                    "name": name,
                }
            )
            continue
        pid = next(iter(matches))
        key = (pid, role)
        if key not in seen:
            attributions.append({"object": pid, "roles": [role]})
            seen.add(key)
    return attributions, unresolved


def validate_creator_roles(items: list[SourceItem]) -> None:
    """Reject new Zotero role values before duplicate selection can hide them."""
    unsupported: list[dict[str, Any]] = []
    for item in items:
        for index, creator in enumerate(creator_records(item)):
            creator_type = clean_text(creator.get("creatorType"))
            if creator_type not in CREATOR_ROLES:
                unsupported.append(
                    {
                        "creator_index": index,
                        "creator_type": creator_type or "<missing>",
                        "item_key": item.key,
                        "name": creator_name(creator),
                    }
                )
    if unsupported:
        raise ValueError(
            "Selected Zotero items contain unsupported creator roles: "
            + json.dumps(unsupported, ensure_ascii=False, sort_keys=True)
        )


def resolve_topics(
    item: SourceItem, topics: dict[str, str]
) -> tuple[list[str], list[dict[str, str]]]:
    resolved: set[str] = set()
    unresolved: list[dict[str, str]] = []
    for tag in item.data.get("tags", []):
        value = clean_text(tag.get("tag")) if isinstance(tag, dict) else clean_text(tag)
        if not value:
            continue
        if pid := topics.get(normalize_name(value)):
            resolved.add(pid)
        else:
            unresolved.append({"item_key": item.key, "tag": value})
    return sorted(resolved), unresolved


def publication_attributes(data: dict[str, Any]) -> list[dict[str, str]]:
    attributes: list[dict[str, str]] = []
    locator_parts = [
        clean_text(data.get("volume")),
        clean_text(data.get("issue")),
        clean_text(data.get("pages")),
    ]
    if locator := ", ".join(part for part in locator_parts if part):
        attributes.append({"predicate": "bibo:locator", "value": locator})
    for predicate, field in (
        ("dcterms:issued", "date"),
        ("dcterms:language", "language"),
        ("dcterms:rights", "rights"),
    ):
        if value := clean_text(data.get(field)):
            attributes.append({"predicate": predicate, "value": value})
    return attributes


def venue_for(data: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    issns = extract_issns(data.get("ISSN"))
    title = clean_text(data.get("publicationTitle") or data.get("proceedingsTitle"))
    if not issns:
        return None, title or None
    pid = f"ISSN:{issns[0]}"
    item_type = clean_text(data.get("itemType"))
    kind = "bibo:Proceedings" if item_type == "conferencePaper" else "bibo:Journal"
    venue: dict[str, Any] = {
        "pid": pid,
        "identifiers": [
            {"notation": issn, "schema_type": "dlthings:ISSN"} for issn in issns
        ],
        "kind": kind,
    }
    if title:
        venue["display_label"] = title
        venue["title"] = title
    return venue, None


def build_record(
    preferred: SourceItem,
    supporting: list[SourceItem],
    class_name: str,
    kind: str | None,
    group_id: int,
    people: dict[str, str],
    organizations: dict[str, str],
    topics: dict[str, str],
    creator_mappings: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    data = preferred.data
    doi = preferred.doi
    record: dict[str, Any] = {
        "pid": canonical_pid(preferred, doi, group_id),
        "identifiers": identifiers_for(supporting, doi, group_id),
    }
    title = clean_text(data.get("title"))
    if class_name == "XYZInstrument":
        if title:
            record["name"] = title
    elif title:
        record["title"] = title
    if title:
        record["display_label"] = title
    if description := clean_text(data.get("abstractNote")):
        record["description"] = description
    if kind:
        record["kind"] = kind

    attributions, unresolved_creators = resolve_creators(
        preferred, people, organizations, creator_mappings
    )
    if attributions:
        record["attributed_to"] = attributions
    about, unresolved_topics = resolve_topics(preferred, topics)
    if about:
        record["about"] = about

    venue = None
    unresolved_venue = None
    if class_name == "XYZPublication":
        attributes = publication_attributes(data)
        if attributes:
            record["attributes"] = attributes
        venue, unresolved_venue = venue_for(data)
        event: dict[str, str] = {"object": "obo:IAO_0000444"}
        if venue:
            event["at_location"] = str(venue["pid"])
        if match := FULL_DATE_RE.match(clean_text(data.get("date"))):
            event["at_time"] = match.group(1)
        if len(event) > 1:
            record["generated_by"] = [event]

    issues = {
        "unresolved_creators": unresolved_creators,
        "unresolved_topics": unresolved_topics,
    }
    if unresolved_venue:
        issues["unresolved_venue"] = {
            "item_key": preferred.key,
            "title": unresolved_venue,
        }
    if not title:
        issues["missing_title"] = {"item_key": preferred.key}
    return record, issues, venue


def duplicate_details(group: list[SourceItem], preferred: SourceItem) -> dict[str, Any]:
    conflict_fields: dict[str, list[str]] = {}
    for field in ("itemType", "title", "date", "publicationTitle", "DOI", "url"):
        values = sorted(
            {
                clean_text(item.data.get(field))
                for item in group
                if clean_text(item.data.get(field))
            }
        )
        if len(values) > 1:
            conflict_fields[field] = values
    return {
        "doi": preferred.doi,
        "item_keys": sorted(item.key for item in group),
        "preferred_item_key": preferred.key,
        "conflicts": conflict_fields,
    }


def existing_pid_index(root: Path | None, output_dir: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    if root is None or not root.exists():
        return found
    output_resolved = output_dir.resolve()
    for path in root.rglob("*.json"):
        if path.resolve().is_relative_to(output_resolved):
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                records = json.load(stream)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict) and record.get("pid"):
                found[str(record["pid"])].append(str(path))
    return found


def command_transform(args: argparse.Namespace) -> None:
    with args.input.open(encoding="utf-8") as stream:
        snapshot = json.load(stream)
    if not isinstance(snapshot, dict):
        raise ValueError("Input is not a Zotero snapshot produced by the fetch command")
    validate_snapshot(snapshot)

    group_id = int(snapshot["source"]["group_id"])
    included_collections = {
        normalize_collection_name(value) for value in args.include_collection
    }
    if not included_collections:
        raise ValueError("At least one included Zotero collection is required")
    document_collection_classes: dict[str, str] = {}
    for value in args.document_collection:
        collection, separator, kind = value.partition("=")
        collection = normalize_collection_name(collection)
        kind = kind.strip().lower()
        if not separator or not collection or kind not in DOCUMENT_COLLECTION_KINDS:
            raise ValueError(
                "Document collection rules must use NAME="
                "dataset|instrument|publication|registry"
            )
        if collection in document_collection_classes:
            raise ValueError(f"Duplicate document collection rule: {collection}")
        document_collection_classes[collection] = kind
    people = load_entity_index(args.people)
    organizations = load_entity_index(args.organizations)
    topics = load_entity_index(args.topics)
    creator_mappings = load_creator_mappings(args.creator_map)
    known_creator_pids = set(people.values()) | set(organizations.values())
    unknown_mapping_pids = sorted(set(creator_mappings.values()) - known_creator_pids)
    if unknown_mapping_pids:
        raise ValueError(
            f"Creator mappings target unknown people or organizations: {unknown_mapping_pids}"
        )
    for name, pid in creator_mappings.items():
        automatic_matches = {
            value for value in (people.get(name), organizations.get(name)) if value
        }
        if automatic_matches and automatic_matches != {pid}:
            raise ValueError(
                f"Creator mapping conflicts with automatic identity index: {name}"
            )
    items = source_items(snapshot, included_collections)
    excluded_external: list[dict[str, Any]] = []
    unfiled: list[dict[str, Any]] = []
    selected: list[SourceItem] = []
    for item in items:
        if item.selected:
            selected.append(item)
        elif not item.collections:
            unfiled.append({"doi": item.doi, "item_key": item.key})
        else:
            excluded_external.append(
                {"collections": list(item.collections), "item_key": item.key}
            )

    validate_creator_roles(selected)
    creator_mapping_usage = Counter(
        normalize_name(creator_name(creator))
        for item in selected
        for creator in creator_records(item)
        if normalize_name(creator_name(creator)) in creator_mappings
    )

    groups: dict[str, list[SourceItem]] = defaultdict(list)
    for item in selected:
        key = f"doi:{item.doi}" if item.doi else f"zotero:{item.key}"
        groups[key].append(item)

    selected_dois = {item.doi for item in selected if item.doi}
    attached_unfiled: list[str] = []
    for item in items:
        if not item.selected and not item.collections and item.doi in selected_dois:
            groups[f"doi:{item.doi}"].append(item)
            attached_unfiled.append(item.key)

    output: dict[str, list[dict[str, Any]]] = {
        class_name: [] for class_name in TARGET_CLASSES
    }
    report: dict[str, Any] = {
        "source": snapshot.get("source", {}),
        "excluded_external": sorted(
            excluded_external, key=lambda value: value["item_key"]
        ),
        "unfiled": sorted(unfiled, key=lambda value: value["item_key"]),
        "attached_unfiled_duplicates": sorted(attached_unfiled),
        "duplicate_dois": [],
        "review_items": [],
        "unresolved_creators": [],
        "unresolved_topics": [],
        "unresolved_venues": [],
        "missing_titles": [],
        "venue_conflicts": [],
        "creator_mapping": {
            "source": str(args.creator_map) if args.creator_map else None,
            "alias_count": len(creator_mappings),
            "target_count": len(set(creator_mappings.values())),
            "usage": [
                {
                    "normalized_name": name,
                    "occurrences": count,
                    "pid": creator_mappings[name],
                }
                for name, count in sorted(creator_mapping_usage.items())
            ],
        },
    }
    venues: dict[str, dict[str, Any]] = {}

    for group_key in sorted(groups):
        group = groups[group_key]
        selected_group = [item for item in group if item.selected]
        classifications = [
            (item, *classify(item, document_collection_classes))
            for item in selected_group
        ]
        class_names = {
            class_name for _, class_name, _, _ in classifications if class_name
        }
        if len(class_names) != 1:
            report["review_items"].append(
                {
                    "group": group_key,
                    "item_keys": sorted(item.key for item in group),
                    "reason": "conflicting or missing class assignments",
                    "classifications": [
                        {
                            "class": class_name,
                            "item_key": item.key,
                            "reason": reason,
                        }
                        for item, class_name, _, reason in classifications
                    ],
                }
            )
            continue

        class_name = next(iter(class_names))
        eligible = [entry for entry in classifications if entry[1] == class_name]
        preferred, _, kind, _ = max(eligible, key=lambda entry: completeness(entry[0]))
        record, issues, venue = build_record(
            preferred,
            group,
            class_name,
            kind,
            group_id,
            people,
            organizations,
            topics,
            creator_mappings,
        )
        output[class_name].append(record)

        report["unresolved_creators"].extend(issues["unresolved_creators"])
        report["unresolved_topics"].extend(issues["unresolved_topics"])
        if issues.get("unresolved_venue"):
            report["unresolved_venues"].append(issues["unresolved_venue"])
        if issues.get("missing_title"):
            report["missing_titles"].append(issues["missing_title"])

        if venue:
            pid = str(venue["pid"])
            if pid in venues and venues[pid] != venue:
                report["venue_conflicts"].append(
                    {"existing": venues[pid], "incoming": venue, "pid": pid}
                )
            else:
                venues[pid] = venue
        if len(group) > 1 and preferred.doi:
            report["duplicate_dois"].append(duplicate_details(group, preferred))

    output["XYZPublicationVenue"] = list(venues.values())
    for class_name, records in output.items():
        records.sort(key=lambda record: str(record["pid"]))
        atomic_write_json(args.output_dir / f"{class_name}.json", records)

    pid_index = existing_pid_index(args.existing_data_root, args.output_dir)
    report["existing_pid_collisions"] = [
        {
            "class": class_name,
            "files": sorted(pid_index[record["pid"]]),
            "pid": record["pid"],
        }
        for class_name, records in output.items()
        for record in records
        if record["pid"] in pid_index
    ]

    title_index: dict[str, set[str]] = defaultdict(set)
    for records in output.values():
        for record in records:
            title = record.get("title") or record.get("name")
            if normalized := normalize_name(title):
                title_index[normalized].add(str(record["pid"]))
    report["title_collisions"] = [
        {"normalized_title": title, "pids": sorted(pids)}
        for title, pids in sorted(title_index.items())
        if len(pids) > 1
    ]
    report["counts"] = {
        "candidate_records": {name: len(records) for name, records in output.items()},
        "collection_memberships": dict(
            sorted(Counter(name for item in items for name in item.collections).items())
        ),
        "excluded_external": len(excluded_external),
        "selected_top_level_items": len(selected),
        "top_level_items": len(items),
        "unfiled": len(unfiled),
    }
    for key in (
        "duplicate_dois",
        "existing_pid_collisions",
        "missing_titles",
        "review_items",
        "title_collisions",
        "unresolved_creators",
        "unresolved_topics",
        "unresolved_venues",
        "venue_conflicts",
    ):
        report[key] = sorted(
            report[key], key=lambda value: json.dumps(value, sort_keys=True)
        )
    atomic_write_json(args.report, report)
    print(
        f"Rendered {sum(len(records) for records in output.values())} candidate "
        f"records to {args.output_dir}; review {args.report}."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="Fetch a full public Zotero snapshot")
    fetch.add_argument("--group-id", type=int, required=True)
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--expected-library-version", type=int)
    fetch.add_argument(
        "--snapshot-attempts",
        type=int,
        default=MAX_SNAPSHOT_ATTEMPTS,
        help="retry a full read if the public library changes during pagination",
    )
    fetch.add_argument(
        "--user-agent",
        default="orinoco-lite Zotero adapter/1",
    )
    fetch.set_defaults(handler=command_fetch)

    transform = commands.add_parser(
        "transform", help="Render schema-class candidate JSON arrays"
    )
    transform.add_argument("--input", type=Path, required=True)
    transform.add_argument("--output-dir", type=Path, required=True)
    transform.add_argument("--report", type=Path, required=True)
    transform.add_argument("--people", type=Path, action="append", default=[])
    transform.add_argument("--organizations", type=Path, action="append", default=[])
    transform.add_argument("--topics", type=Path, action="append", default=[])
    transform.add_argument("--creator-map", type=Path)
    transform.add_argument("--existing-data-root", type=Path)
    transform.add_argument(
        "--include-collection",
        action="append",
        default=[],
        help="normalized Zotero collection name to retain (repeatable)",
    )
    transform.add_argument(
        "--document-collection",
        action="append",
        default=[],
        metavar="NAME=KIND",
        help="classify document items by collection (repeatable)",
    )
    transform.set_defaults(handler=command_transform)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
