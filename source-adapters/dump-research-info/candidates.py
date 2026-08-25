#!/usr/bin/env python3
"""Build the Milestone 5 ``dump-research-info`` candidate plan."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path, PurePosixPath
import sys
from types import ModuleType
from typing import Any, Mapping
from urllib.parse import quote

import yaml

from linkml_runtime.utils.schemaview import SchemaView
from orinoco_lite.annotations import compact_enrichment_view
from orinoco_lite.canonical import canonical_json_bytes
from orinoco_lite.candidates import Candidate, CandidatePlan
from orinoco_lite.decisions import load_decision_cache
from orinoco_lite.enrichment import (
    resolve_enrichment_slot,
    update_data_property,
    update_multivalued_object_property,
    update_schema_data_property,
)


ADAPTER = "dump-research-info"
ADAPTER_VERSION = "2"
SOURCE_NAMESPACE = "https://github.com/con/dump-research-info"
ROLE_SOURCE_DIRECTORY = "data/pool_psychoinformatics_de"
DECISION_CACHE = PurePosixPath(
    "source-adapters/dump-research-info/policy/curation-decisions.yaml"
)

# This is source-specific mapping policy, derived from the pinned source Things
# Schema. A new source field must be reviewed rather than silently omitted.
TOPICAL_FIELDS = frozenset(
    {
        "about",
        "additional_names",
        "at_location",
        "broad_mappings",
        "close_mappings",
        "description",
        "display_label",
        "family_name",
        "formatted_name",
        "given_name",
        "name",
        "nickname",
        "part_of",
        "short_name",
        "title",
    }
)
OBJECT_COLLECTIONS = frozenset(
    {"associated_with", "attributed_to", "generated_by", "identifiers"}
)
STRUCTURAL_FIELDS = frozenset({"pid", "schema_type"})
SUPPORTED_FIELDS = frozenset(
    {
        *STRUCTURAL_FIELDS,
        *TOPICAL_FIELDS,
        *OBJECT_COLLECTIONS,
        "attributes",
    }
)


class DumpResearchInfoCandidateError(RuntimeError):
    """Report an unsafe base or unsupported source-to-Thing mapping."""


def _load_metadata_adapter(path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "orinoco_dump_research_info_metadata_adapter", path
    )
    if specification is None or specification.loader is None:
        raise DumpResearchInfoCandidateError(
            f"Cannot load dump-research-info mapping policy {path}"
        )
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _load_companion(root: Path, record_path: str) -> dict[str, object] | None:
    path = root / "metadata/overlays/annotations" / PurePosixPath(record_path)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise DumpResearchInfoCandidateError(
            f"Annotation companion is not an ordinary file: {path}"
        )
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise DumpResearchInfoCandidateError(
            f"Cannot load annotation companion {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise DumpResearchInfoCandidateError(
            f"Annotation companion must be a mapping: {path}"
        )
    return value


def _source_record_uri(
    source_directory: str, source_class: str, source_pid: str
) -> str:
    """Identify a logical source row independently of its Git revision."""

    return (
        f"{SOURCE_NAMESPACE}/blob/main/{source_directory}/"
        f"{quote(source_class, safe='')}.json#record={quote(source_pid, safe='')}"
    )


def _mapping_sequence(value: object, *, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise DumpResearchInfoCandidateError(
            f"Source field {field} must be a list of assertion mappings"
        )
    return [dict(item) for item in value]


def _apply_explicit_attributes(
    record: Mapping[str, Any],
    companion: Mapping[str, object] | None,
    value: object,
    *,
    owner_id: str,
    source_id: str,
    reserved_predicates: set[str],
) -> tuple[dict[str, Any], dict[str, object] | None]:
    """Apply the legacy source's already-qualified data assertions upstream."""

    attributes = _mapping_sequence(value, field="attributes")
    grouped: dict[str, list[object]] = {}
    predicate_order: list[str] = []
    for assertion in attributes:
        if set(assertion) != {"predicate", "schema_type", "value"}:
            raise DumpResearchInfoCandidateError(
                "Source attributes must contain exactly predicate, schema_type, and value"
            )
        predicate = assertion.get("predicate")
        if (
            not isinstance(predicate, str)
            or not predicate
            or assertion.get("schema_type") != "dlthings:AttributeSpecification"
            or not isinstance(assertion.get("value"), str)
        ):
            raise DumpResearchInfoCandidateError(
                "Source attributes must be string-valued AttributeSpecification "
                "mappings"
            )
        if predicate in reserved_predicates:
            raise DumpResearchInfoCandidateError(
                f"Explicit source predicate {predicate} conflicts with a topical "
                "field mapping"
            )
        if predicate not in grouped:
            predicate_order.append(predicate)
            grouped[predicate] = []
        grouped[predicate].append(deepcopy(assertion["value"]))

    current_record = dict(record)
    current_companion = dict(companion) if companion is not None else None
    working = compact_enrichment_view(current_record, current_companion)
    stored_attributes = working.get("attributes", [])
    if not isinstance(stored_attributes, list) or not all(
        isinstance(assertion, Mapping) for assertion in stored_attributes
    ):
        raise DumpResearchInfoCandidateError(
            "Canonical attributes must be a list of assertion mappings"
        )
    previously_owned = {
        str(assertion["predicate"])
        for assertion in stored_attributes
        if isinstance(assertion.get("predicate"), str)
        and isinstance(assertion.get("annotations"), Mapping)
        and assertion["annotations"].get("pav:importedBy") == owner_id
        and assertion["predicate"] not in reserved_predicates
    }
    predicates = [
        *predicate_order,
        *sorted(previously_owned - set(predicate_order)),
    ]
    for predicate in predicates:
        values = grouped.get(predicate, [])
        update = update_data_property(
            current_record,
            current_companion,
            predicate=predicate,
            value=values[0] if len(values) == 1 else values,
            owner_id=owner_id,
            source_id=source_id,
        )
        current_record, current_companion = update.record, update.companion
    return current_record, current_companion


def _apply_source_claim(
    target: Any,
    baseline_record: Mapping[str, Any] | None,
    baseline_companion: Mapping[str, object] | None,
    *,
    adapter_agent_pid: str,
    schema: SchemaView,
) -> tuple[dict[str, Any], dict[str, object] | None]:
    source = dict(target.transformed_record)
    unknown = sorted(set(source) - SUPPORTED_FIELDS)
    if unknown:
        raise DumpResearchInfoCandidateError(
            f"{target.source_record_id}: unsupported source fields: "
            + ", ".join(unknown)
        )
    expected_type = f"xyzri:{target.source_class}"
    if source.get("schema_type") != expected_type:
        raise DumpResearchInfoCandidateError(
            f"{target.source_record_id}: transformed schema_type is not {expected_type}"
        )

    if baseline_record is None:
        record: dict[str, Any] = {
            "pid": target.target_pid,
            "schema_type": expected_type,
        }
    else:
        record = deepcopy(dict(baseline_record))
        if record.get("schema_type") != expected_type:
            raise DumpResearchInfoCandidateError(
                f"{target.source_record_id}: canonical schema_type does not match its class"
            )
    companion = (
        deepcopy(dict(baseline_companion)) if baseline_companion is not None else None
    )
    source_id = _source_record_uri(
        target.source_directory,
        target.source_class,
        target.source_pid,
    )
    reserved_predicates = {
        resolve_enrichment_slot(schema, field).predicate for field in TOPICAL_FIELDS
    }

    # Iterate every reviewed mapping so source omission drives an empty pinned
    # update. This removes only obsolete assertions owned by this adapter;
    # topicals and human- or differently owned assertions remain untouched.
    for field in sorted(SUPPORTED_FIELDS - STRUCTURAL_FIELDS):
        present = field in source
        value = source[field] if present else []
        if field in TOPICAL_FIELDS:
            update = update_schema_data_property(
                record,
                companion,
                schema=schema,
                topical_slot=field,
                value=deepcopy(value),
                owner_id=adapter_agent_pid,
                source_id=source_id,
                populate_topical=present,
            )
            record, companion = update.record, update.companion
            continue
        if field in OBJECT_COLLECTIONS:
            update = update_multivalued_object_property(
                record,
                companion,
                slot=field,
                values=_mapping_sequence(value, field=field),
                owner_id=adapter_agent_pid,
                source_id=source_id,
            )
            record, companion = update.record, update.companion
            continue
        if field == "attributes":
            record, companion = _apply_explicit_attributes(
                record,
                companion,
                value,
                owner_id=adapter_agent_pid,
                source_id=source_id,
                reserved_predicates=reserved_predicates,
            )
            continue
        raise AssertionError(f"Supported source field has no updater: {field}")
    return record, companion


def _semantic_source_claim(
    target: Any,
    *,
    adapter_agent_pid: str,
    schema: SchemaView,
) -> dict[str, Any]:
    """Return the baseline-independent semantic output of this source row."""

    mapped, _companion = _apply_source_claim(
        target,
        None,
        None,
        adapter_agent_pid=adapter_agent_pid,
        schema=schema,
    )
    # Candidate hashing already binds the target PID and record path. Keep the
    # policy-created class and every other unannotated assertion in the mapped
    # fragment; the split companion's PAV is deliberately excluded.
    return {key: deepcopy(value) for key, value in mapped.items() if key != "pid"}


def _candidate(
    root: Path,
    target: Any,
    *,
    adapter_agent_pid: str,
    schema: SchemaView,
) -> Candidate | None:
    baseline_companion = _load_companion(root, target.record_path)
    proposed_record, proposed_companion = _apply_source_claim(
        target,
        target.baseline_record,
        baseline_companion,
        adapter_agent_pid=adapter_agent_pid,
        schema=schema,
    )
    baseline_record = (
        dict(target.baseline_record) if target.baseline_record is not None else None
    )
    if baseline_record == proposed_record and baseline_companion == proposed_companion:
        return None
    if target.source_directory == ROLE_SOURCE_DIRECTORY and baseline_record is not None:
        try:
            exact_semantic_match = canonical_json_bytes(
                baseline_record
            ) == canonical_json_bytes(target.transformed_record)
        except (TypeError, ValueError) as error:
            raise DumpResearchInfoCandidateError(
                f"{target.source_record_id}: canonical role is not deterministic JSON"
            ) from error
        if exact_semantic_match:
            # The authoritative-pool lookup above is still mandatory. Once it
            # establishes an exact canonical match, do not manufacture a
            # proposal whose only effect is duplicate qualification/PAV.
            return None
        raise DumpResearchInfoCandidateError(
            f"{target.source_record_id}: authoritative role differs from the "
            "same-PID canonical role; reconcile that prerequisite before "
            "importing dependent primary records"
        )
    return Candidate(
        source_namespace=SOURCE_NAMESPACE,
        source_record_id=target.source_record_id,
        pid=target.target_pid,
        record_path=target.record_path,
        baseline_record=baseline_record,
        proposed_record=proposed_record,
        baseline_companion=baseline_companion,
        proposed_companion=proposed_companion,
        source_claim=_semantic_source_claim(
            target,
            adapter_agent_pid=adapter_agent_pid,
            schema=schema,
        ),
    )


def build_candidate_plan(
    root: Path,
    source_checkout: Path,
    *,
    metadata_base: str,
    expected_source_commit: str,
    adapter_agent_pid: str,
    schema: SchemaView,
    trusted_root: Path | None = None,
) -> CandidatePlan:
    """Build the active-decision-filtered plan for one exact source/base pair."""

    downstream = root.resolve()
    trusted = (root if trusted_root is None else trusted_root).resolve()
    if not isinstance(schema, SchemaView):
        raise DumpResearchInfoCandidateError(
            "dump-research-info requires the pinned Things SchemaView"
        )
    metadata_adapter = _load_metadata_adapter(
        trusted / "source-adapters/dump-research-info/metadata_adapter.py"
    )
    canonical = metadata_adapter.load_yaml_records(downstream)
    if adapter_agent_pid not in canonical:
        raise DumpResearchInfoCandidateError(
            "dump-research-info adapter agent PID must identify a canonical "
            f"versioned Thing: {adapter_agent_pid}"
        )
    targets, source_coordinate = metadata_adapter.build_source_targets(
        source_checkout,
        downstream,
        expected_source_commit=expected_source_commit,
    )
    candidates = tuple(
        candidate
        for target in targets
        if (
            candidate := _candidate(
                downstream,
                target,
                adapter_agent_pid=adapter_agent_pid,
                schema=schema,
            )
        )
        is not None
    )
    full_plan = CandidatePlan(
        adapter=ADAPTER,
        adapter_version=ADAPTER_VERSION,
        adapter_agent_pid=adapter_agent_pid,
        source_namespace=SOURCE_NAMESPACE,
        source_coordinate=source_coordinate,
        metadata_base=metadata_base,
        candidates=candidates,
    )
    cache = load_decision_cache(downstream / DECISION_CACHE, adapter=ADAPTER)
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


__all__ = [
    "ADAPTER",
    "ADAPTER_VERSION",
    "DumpResearchInfoCandidateError",
    "build_candidate_plan",
]
