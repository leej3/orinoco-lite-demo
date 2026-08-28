#!/usr/bin/env python3
"""Build deterministic Zotero candidate plans from reviewed source evidence."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Mapping
from urllib.parse import quote

from linkml_runtime.utils.schemaview import SchemaView
from orinoco_lite.annotations import validate_annotation_companion
from orinoco_lite.candidates import Candidate, CandidatePlan
from orinoco_lite.decisions import load_decision_cache
from orinoco_lite.enrichment import (
    EnrichmentUpdate,
    update_data_property,
    update_multivalued_object_property,
    update_schema_data_property,
)
import yaml


ADAPTER_ID = "zotero"
ADAPTER_VERSION = "1"
DECISION_CACHE = Path("site-specific/curation-records/zotero.yaml")
ZOTERO_NOTATION = re.compile(r"^zotero:group:(\d+):item:([A-Z0-9]+)$")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_ATTRIBUTE_PREDICATES = (
    "bibo:locator",
    "dcterms:issued",
    "dcterms:language",
    "dcterms:rights",
)
CLAIM_ENVELOPE_FIELDS = frozenset({"pid"})
RENDERED_FIELDS = frozenset(
    {
        "about",
        "attributed_to",
        "attributes",
        "description",
        "display_label",
        "generated_by",
        "identifiers",
        "kind",
        "pid",
        "schema_type",
        "title",
    }
)


class ZoteroCandidateError(RuntimeError):
    """Report an invalid frozen source, policy, or proposal target."""


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise ZoteroCandidateError(f"Cannot load Zotero dependency {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def load_metadata_adapter(root: Path) -> ModuleType:
    return load_module(
        "orinoco_zotero_metadata_adapter_for_candidates",
        root / ".orinoco-lite/source-adapters/zotero/metadata_adapter.py",
    )


def load_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = yaml.safe_load(raw)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ZoteroCandidateError(f"Cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise ZoteroCandidateError(f"{label} must be a mapping: {path}")
    return value


def load_publications(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ZoteroCandidateError(
            f"Cannot read Zotero candidates {path}: {error}"
        ) from error
    if not isinstance(value, list) or not all(
        isinstance(record, dict) for record in value
    ):
        raise ZoteroCandidateError(
            "Zotero publication candidates must be an array of objects"
        )
    return value


def canonical_state(
    root: Path,
) -> dict[str, tuple[Path, dict[str, Any], dict[str, Any] | None]]:
    """Load semantic record and companion state by PID."""

    records_root = root / "site-specific/metadata/records"
    annotations_root = root / "site-specific/metadata/overlays/annotations"
    if records_root.is_symlink() or not records_root.is_dir():
        raise ZoteroCandidateError(
            "Canonical record root must be an ordinary directory"
        )
    result: dict[str, tuple[Path, dict[str, Any], dict[str, Any] | None]] = {}
    path_to_pid: dict[Path, str] = {}
    for path in sorted(records_root.rglob("*")):
        if path.is_symlink():
            raise ZoteroCandidateError(
                f"Canonical metadata cannot be a symlink: {path}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise ZoteroCandidateError(f"Canonical metadata is not a file: {path}")
        if path.name == ".dumpthings.yaml" and path.parent == records_root:
            continue
        if path.suffix.lower() not in {".yaml", ".yml"} or any(
            part.startswith(".") for part in path.relative_to(records_root).parts
        ):
            raise ZoteroCandidateError(
                f"Canonical record root contains unsupported content: {path}"
            )
        record = load_mapping(path, label="canonical record")
        pid = record.get("pid")
        if not isinstance(pid, str) or not pid or pid in result:
            raise ZoteroCandidateError(
                f"Canonical record PID is invalid or duplicated: {path}"
            )
        relative = path.relative_to(records_root)
        result[pid] = (relative, record, None)
        path_to_pid[relative] = pid

    if annotations_root.exists():
        if annotations_root.is_symlink() or not annotations_root.is_dir():
            raise ZoteroCandidateError(
                "Annotation overlay root must be an ordinary directory"
            )
        for path in sorted(annotations_root.rglob("*")):
            if path.is_symlink():
                raise ZoteroCandidateError(
                    f"Annotation overlay contains unsupported content: {path}"
                )
            if path.is_dir():
                continue
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
                raise ZoteroCandidateError(
                    f"Annotation overlay contains unsupported content: {path}"
                )
            relative = path.relative_to(annotations_root)
            pid = path_to_pid.get(relative)
            if pid is None:
                raise ZoteroCandidateError(
                    f"Annotation companion has no mirrored record: {path}"
                )
            companion = load_mapping(path, label="annotation companion")
            assertions = companion.get("assertions")
            if not isinstance(assertions, list) or not assertions:
                raise ZoteroCandidateError(
                    f"Annotation companion must contain an assertion: {path}"
                )
            record_path, record, previous = result[pid]
            if previous is not None:
                raise ZoteroCandidateError(
                    f"Canonical record has multiple annotation companions: {pid}"
                )
            validate_annotation_companion(record, companion)
            result[pid] = (record_path, record, companion)
    return result


def source_identity(record: Mapping[str, Any], group_id: int) -> tuple[str, str]:
    """Return the stable Zotero item-set identity and logical source URI.

    DOI-derived publication PIDs are proposal data, not source identity.
    Correcting a DOI therefore reopens the same source claim. Duplicate items
    intentionally use one composite identity made from sorted Zotero item keys.
    """

    matches: list[tuple[str, str]] = []
    identifiers = record.get("identifiers", [])
    if not isinstance(identifiers, list):
        raise ZoteroCandidateError(f"{record.get('pid')}: identifiers must be a list")
    for identifier in identifiers:
        if not isinstance(identifier, dict):
            continue
        notation = identifier.get("notation")
        match = ZOTERO_NOTATION.fullmatch(str(notation))
        if match is not None:
            matches.append((match.group(1), match.group(2)))
    if not matches or any(group != str(group_id) for group, _key in matches):
        raise ZoteroCandidateError(
            f"{record.get('pid')}: expected Zotero identifiers for group {group_id}"
        )
    keys = sorted({key for _group, key in matches})
    source_record_id = (
        f"item:{keys[0]}" if len(keys) == 1 else f"items:{','.join(keys)}"
    )
    if len(keys) == 1:
        imported_from = f"https://api.zotero.org/groups/{group_id}/items/{keys[0]}"
    else:
        imported_from = (
            f"https://api.zotero.org/groups/{group_id}/items?itemKey="
            f"{quote(','.join(keys), safe='')}"
        )
    return source_record_id, imported_from


def _apply(update: EnrichmentUpdate) -> tuple[dict[str, Any], dict[str, Any] | None]:
    companion = (
        deepcopy(dict(update.companion)) if update.companion is not None else None
    )
    return deepcopy(update.record), companion


def _source_attributes(record: Mapping[str, Any]) -> dict[str, list[str]]:
    grouped = {predicate: [] for predicate in SOURCE_ATTRIBUTE_PREDICATES}
    attributes = record.get("attributes", [])
    if not isinstance(attributes, list):
        raise ZoteroCandidateError(f"{record.get('pid')}: attributes must be a list")
    for attribute in attributes:
        if not isinstance(attribute, dict) or set(attribute) != {
            "predicate",
            "schema_type",
            "value",
        }:
            raise ZoteroCandidateError(
                f"{record.get('pid')}: source attribute has an unsupported shape"
            )
        predicate = attribute.get("predicate")
        value = attribute.get("value")
        if (
            predicate not in grouped
            or attribute.get("schema_type") != "dlthings:AttributeSpecification"
            or not isinstance(value, str)
        ):
            raise ZoteroCandidateError(
                f"{record.get('pid')}: source attribute is outside the reviewed mapping"
            )
        grouped[str(predicate)].append(value)
    for predicate, values in grouped.items():
        if len(values) != len(set(values)):
            raise ZoteroCandidateError(
                f"{record.get('pid')}: source repeats {predicate} values"
            )
    return grouped


def _object_values(record: Mapping[str, Any], field: str) -> list[dict[str, Any]]:
    value = record.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ZoteroCandidateError(
            f"{record.get('pid')}: {field} must be a list of mappings"
        )
    return [deepcopy(item) for item in value]


def propose_record(
    source_record: Mapping[str, Any],
    baseline_record: Mapping[str, Any] | None,
    baseline_companion: Mapping[str, object] | None,
    *,
    adapter_agent_pid: str,
    imported_from: str,
    schema: SchemaView,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Apply one rendered source record with pinned ownership semantics."""

    unknown_fields = set(source_record) - RENDERED_FIELDS
    if unknown_fields:
        raise ZoteroCandidateError(
            f"{source_record.get('pid')}: rendered source has unsupported fields "
            + ", ".join(sorted(unknown_fields))
        )
    pid = source_record.get("pid")
    schema_type = source_record.get("schema_type")
    if not isinstance(pid, str) or not pid or not isinstance(schema_type, str):
        raise ZoteroCandidateError("Rendered Zotero record has invalid identity")
    if baseline_record is None:
        record: dict[str, Any] = {"pid": pid, "schema_type": schema_type}
        companion: dict[str, Any] | None = None
    else:
        record = deepcopy(dict(baseline_record))
        companion = (
            deepcopy(dict(baseline_companion))
            if baseline_companion is not None
            else None
        )
        if record.get("pid") != pid or record.get("schema_type") != schema_type:
            raise ZoteroCandidateError(
                f"{pid}: source identity or class conflicts with the canonical record"
            )

    for predicate, values in _source_attributes(source_record).items():
        record, companion = _apply(
            update_data_property(
                record,
                companion,
                predicate=predicate,
                value=values,
                owner_id=adapter_agent_pid,
                source_id=imported_from,
            )
        )

    for field, required in (
        ("title", True),
        ("display_label", True),
        ("description", False),
    ):
        value = source_record.get(field)
        if value is None:
            if required:
                raise ZoteroCandidateError(f"{pid}: rendered source lacks {field}")
            record, companion = _apply(
                update_schema_data_property(
                    record,
                    companion,
                    schema=schema,
                    topical_slot=field,
                    value=[],
                    owner_id=adapter_agent_pid,
                    source_id=imported_from,
                    populate_topical=False,
                )
            )
        elif not isinstance(value, str) or not value:
            raise ZoteroCandidateError(f"{pid}: rendered {field} must be a string")
        else:
            record, companion = _apply(
                update_schema_data_property(
                    record,
                    companion,
                    schema=schema,
                    topical_slot=field,
                    value=value,
                    owner_id=adapter_agent_pid,
                    source_id=imported_from,
                )
            )

    kind = source_record.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ZoteroCandidateError(f"{pid}: rendered kind must be a URI string")
    record, companion = _apply(
        update_schema_data_property(
            record,
            companion,
            schema=schema,
            topical_slot="kind",
            value=kind,
            owner_id=adapter_agent_pid,
            source_id=imported_from,
        )
    )

    about = source_record.get("about", [])
    if not isinstance(about, list) or not all(
        isinstance(value, str) and value for value in about
    ):
        raise ZoteroCandidateError(f"{pid}: rendered about must be a list of URIs")
    record, companion = _apply(
        update_schema_data_property(
            record,
            companion,
            schema=schema,
            topical_slot="about",
            value=deepcopy(about),
            owner_id=adapter_agent_pid,
            source_id=imported_from,
            populate_topical=bool(about),
        )
    )

    for field in ("identifiers", "attributed_to"):
        record, companion = _apply(
            update_multivalued_object_property(
                record,
                companion,
                slot=field,
                values=_object_values(source_record, field),
                owner_id=adapter_agent_pid,
                source_id=imported_from,
            )
        )

    curated_generation = deepcopy(source_record.get("generated_by"))
    if curated_generation is not None and (
        not isinstance(curated_generation, list)
        or not all(isinstance(item, dict) for item in curated_generation)
    ):
        raise ZoteroCandidateError(
            f"{pid}: reviewed generated_by policy must contain mappings"
        )
    if baseline_record is None:
        if curated_generation is not None:
            record["generated_by"] = curated_generation
    elif curated_generation is not None:
        existing_generation = record.get("generated_by", [])
        if not isinstance(existing_generation, list) or not all(
            isinstance(item, dict) for item in existing_generation
        ):
            raise ZoteroCandidateError(
                f"{pid}: canonical generated_by must contain mappings"
            )
        missing = [
            expected
            for expected in curated_generation
            if not any(
                all(actual.get(key) == value for key, value in expected.items())
                for actual in existing_generation
            )
        ]
        if missing:
            raise ZoteroCandidateError(
                f"{pid}: reviewed site-owned generated_by is absent; change the "
                "curated field in a separate human curation action"
            )
    return record, companion


def semantic_source_claim(
    source_record: Mapping[str, Any],
    *,
    adapter_agent_pid: str,
    imported_from: str,
    schema: SchemaView,
) -> dict[str, Any]:
    """Return only the adapter-owned unannotated semantic proposal fragment."""

    proposed, _companion = propose_record(
        source_record,
        None,
        None,
        adapter_agent_pid=adapter_agent_pid,
        imported_from=imported_from,
        schema=schema,
    )
    return {
        key: deepcopy(value)
        for key, value in proposed.items()
        if key not in CLAIM_ENVELOPE_FIELDS
    }


def _source_coordinate(source: Mapping[str, Any]) -> dict[str, Any]:
    group_id = source.get("group_id")
    library_version = source.get("library_version")
    content_sha256 = source.get("content_sha256")
    if (
        not isinstance(group_id, int)
        or not isinstance(library_version, int)
        or not isinstance(content_sha256, str)
        or SHA256.fullmatch(content_sha256) is None
    ):
        raise ZoteroCandidateError("The committed Zotero source coordinate is invalid")
    return {
        "content_sha256": f"sha256:{content_sha256}",
        "group_id": group_id,
        "kind": "zotero-public-library",
        "library_version": library_version,
    }


def build_candidate_plan(
    root: Path,
    output: Path,
    *,
    metadata_base: str,
    expected_library_version: int,
    adapter_agent_pid: str,
    schema: SchemaView,
    trusted_root: Path | None = None,
) -> CandidatePlan:
    """Build candidates without writing canonical metadata or decision state."""

    metadata_root = root.resolve()
    trusted = (root if trusted_root is None else trusted_root).resolve()
    if not isinstance(schema, SchemaView):
        raise ZoteroCandidateError("Zotero requires the pinned Things SchemaView")
    build_root = metadata_root / "build"
    if build_root.exists() and build_root.is_symlink():
        raise ZoteroCandidateError("Zotero candidate build root cannot be a symlink")
    output = output.resolve()
    try:
        output.relative_to(build_root)
    except ValueError as error:
        raise ZoteroCandidateError(
            "Zotero candidate output must be below the repository build directory"
        ) from error
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            raise ZoteroCandidateError(
                f"Zotero candidate output must be an ordinary directory: {output}"
            )
    else:
        output.mkdir(parents=True)
    metadata_adapter = load_metadata_adapter(trusted)
    snapshot_path = trusted / "site-specific/sources/zotero/content/snapshot.json"
    publications_path = (
        trusted / "site-specific/sources/zotero/evidence/candidates/XYZPublication.json"
    )
    policy_path = trusted / "site-specific/sources/zotero/policy/site-policy.yaml"
    snapshot = metadata_adapter.load_json(snapshot_path)
    if not isinstance(snapshot, dict):
        raise ZoteroCandidateError("The committed Zotero snapshot must be an object")
    ingest, site_export = metadata_adapter.load_tools(trusted)
    ingest.validate_snapshot(snapshot)
    source = snapshot.get("source")
    if not isinstance(source, dict):
        raise ZoteroCandidateError(
            "The committed Zotero snapshot has no source metadata"
        )
    coordinate = _source_coordinate(source)
    if coordinate["library_version"] != expected_library_version:
        raise ZoteroCandidateError(
            "Zotero fixture moved: expected "
            f"{expected_library_version}, found {coordinate['library_version']}"
        )
    group_id = int(coordinate["group_id"])

    canonical = canonical_state(metadata_root)
    identity = canonical.get(adapter_agent_pid)
    if identity is None or identity[1].get("schema_type") != "xyzri:XYZInstrument":
        raise ZoteroCandidateError(
            "Zotero adapter provenance identity must identify a reviewed "
            f"xyzri:XYZInstrument record: {adapter_agent_pid}"
        )
    source_publications = load_publications(publications_path)
    source_by_pid = {str(record.get("pid")): record for record in source_publications}
    if len(source_by_pid) != len(source_publications) or "None" in source_by_pid:
        raise ZoteroCandidateError(
            "Zotero publication candidate PIDs are invalid or duplicated"
        )

    rendered_root, rendered_report_path = metadata_adapter.export_site_publications(
        site_export,
        publications_path,
        snapshot_path,
        policy_path,
        output,
    )
    rendered: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(rendered_root.glob("*.yaml")):
        record = load_mapping(path, label="rendered publication")
        pid = record.get("pid")
        if not isinstance(pid, str) or not pid or pid in rendered:
            raise ZoteroCandidateError(
                f"Rendered Zotero PID is invalid or duplicated: {path}"
            )
        rendered[pid] = (path, record)
    rendered_report = metadata_adapter.load_json(rendered_report_path)
    if not isinstance(rendered_report, dict):
        raise ZoteroCandidateError("Zotero site export report must be an object")
    pid_map = rendered_report.get("pid_map")
    if not isinstance(pid_map, list):
        raise ZoteroCandidateError("Zotero site export report has no PID map")

    source_namespace = f"zotero:group:{group_id}"
    candidates: list[Candidate] = []
    seen_source_pids: set[str] = set()
    seen_site_pids: set[str] = set()
    for mapping in pid_map:
        if not isinstance(mapping, dict) or set(mapping) != {"source_pid", "site_pid"}:
            raise ZoteroCandidateError("Zotero PID map contains an invalid entry")
        source_pid = mapping.get("source_pid")
        site_pid = mapping.get("site_pid")
        if not isinstance(source_pid, str) or not isinstance(site_pid, str):
            raise ZoteroCandidateError("Zotero PID map entry is incomplete")
        if source_pid in seen_source_pids or site_pid in seen_site_pids:
            raise ZoteroCandidateError("Zotero PID map is not one-to-one")
        seen_source_pids.add(source_pid)
        seen_site_pids.add(site_pid)
        source_record = source_by_pid.get(source_pid)
        rendered_entry = rendered.get(site_pid)
        if source_record is None or rendered_entry is None:
            raise ZoteroCandidateError(f"Zotero PID map cannot resolve {source_pid}")
        rendered_path, rendered_record = rendered_entry
        source_record_id, imported_from = source_identity(source_record, group_id)
        baseline = canonical.get(site_pid)
        if baseline is None:
            record_path = Path("XYZPublication") / rendered_path.name
            baseline_record = None
            baseline_companion = None
        else:
            record_path, baseline_record, baseline_companion = baseline
        proposed_record, proposed_companion = propose_record(
            rendered_record,
            baseline_record,
            baseline_companion,
            adapter_agent_pid=adapter_agent_pid,
            imported_from=imported_from,
            schema=schema,
        )
        source_claim = semantic_source_claim(
            rendered_record,
            adapter_agent_pid=adapter_agent_pid,
            imported_from=imported_from,
            schema=schema,
        )
        if (
            proposed_record == baseline_record
            and proposed_companion == baseline_companion
        ):
            continue
        candidates.append(
            Candidate(
                source_namespace=source_namespace,
                source_record_id=source_record_id,
                pid=site_pid,
                record_path=record_path.as_posix(),
                baseline_record=baseline_record,
                proposed_record=proposed_record,
                baseline_companion=baseline_companion,
                proposed_companion=proposed_companion,
                source_claim=source_claim,
            )
        )

    if seen_source_pids != set(source_by_pid) or seen_site_pids != set(rendered):
        raise ZoteroCandidateError(
            "Zotero PID map does not cover the complete rendered publication set"
        )
    full_plan = CandidatePlan(
        adapter=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        adapter_agent_pid=adapter_agent_pid,
        source_namespace=source_namespace,
        source_coordinate=coordinate,
        metadata_base=metadata_base,
        candidates=candidates,
    )
    cache = load_decision_cache(metadata_root / DECISION_CACHE, adapter=ADAPTER_ID)
    required = cache.candidates_requiring_review(full_plan)
    return CandidatePlan(
        adapter=full_plan.adapter,
        adapter_version=full_plan.adapter_version,
        adapter_agent_pid=full_plan.adapter_agent_pid,
        source_namespace=full_plan.source_namespace,
        source_coordinate=full_plan.source_coordinate,
        metadata_base=full_plan.metadata_base,
        candidates=required,
    )
