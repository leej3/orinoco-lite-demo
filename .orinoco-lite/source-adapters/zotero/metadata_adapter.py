#!/usr/bin/env python3
"""Review a configured public Zotero library without promoting site records."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Mapping

import yaml


ADAPTER_API_VERSION = 1
TARGET_CLASSES = (
    "XYZDataset",
    "XYZDocument",
    "XYZInstrument",
    "XYZPublication",
    "XYZPublicationVenue",
)
REVIEW_QUEUE_FIELDS = (
    "duplicate_dois",
    "missing_titles",
    "review_items",
    "title_collisions",
    "unresolved_creators",
    "unresolved_topics",
    "unresolved_venues",
    "venue_conflicts",
)


class ZoteroAdapterError(RuntimeError):
    """Report an invalid Zotero snapshot, candidate, or site mapping."""


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise ZoteroAdapterError(f"Cannot load Zotero tool {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def load_tools(root: Path) -> tuple[ModuleType, ModuleType]:
    tools = root / ".orinoco-lite" / "source-adapters" / "zotero" / "tools"
    ingest = load_module("zotero_ingest", tools / "zotero_ingest.py")
    site_export = load_module("zotero_site_export", tools / "zotero_site_export.py")
    return ingest, site_export


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ZoteroAdapterError(f"Cannot read JSON {path}: {error}") from error


def snapshot_map(snapshot: Mapping[str, object]) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for collection, prefix in ((snapshot.get("collections"), "collection"), (snapshot.get("items"), "item")):
        if not isinstance(collection, list):
            raise ZoteroAdapterError(f"Zotero snapshot {prefix}s must be a list")
        for record in collection:
            if not isinstance(record, dict):
                raise ZoteroAdapterError(f"Zotero snapshot contains a non-object {prefix}")
            data = record.get("data", record)
            key = data.get("key") if isinstance(data, dict) else None
            identity = f"{prefix}:{key}" if isinstance(key, str) and key else ""
            if not identity or identity in records:
                raise ZoteroAdapterError(f"Zotero snapshot has an invalid {prefix} key")
            records[identity] = record
    return records


def collection_map(snapshot: Mapping[str, object]) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    collections = snapshot.get("collections")
    if not isinstance(collections, list):
        raise ZoteroAdapterError("Zotero snapshot collections must be a list")
    for record in collections:
        data = record.get("data", record) if isinstance(record, dict) else None
        if not isinstance(data, dict):
            raise ZoteroAdapterError("Zotero snapshot contains an invalid collection")
        key = data.get("key")
        name = data.get("name")
        if not isinstance(key, str) or not key or not isinstance(name, str) or not name:
            raise ZoteroAdapterError("Zotero snapshot collection lacks a key or name")
        records[key] = {"key": key, "name": name}
    return records


def candidate_map(directory: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for path in sorted(directory.glob("XYZ*.json")):
        payload = load_json(path)
        if not isinstance(payload, list):
            raise ZoteroAdapterError(f"Candidate file is not a list: {path}")
        for record in payload:
            if not isinstance(record, dict) or not isinstance(record.get("pid"), str):
                raise ZoteroAdapterError(f"Candidate file has an invalid record: {path}")
            pid = str(record["pid"])
            if pid in records:
                raise ZoteroAdapterError(f"Candidate PID is duplicated: {pid}")
            records[pid] = record
    return records


def yaml_map(directory: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for path in sorted(directory.rglob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("pid"), str):
            raise ZoteroAdapterError(f"Canonical YAML has an invalid record: {path}")
        pid = str(payload["pid"])
        if pid in records:
            raise ZoteroAdapterError(f"Canonical PID is duplicated: {pid}")
        records[pid] = payload
    return records


def projection_violations(
    reviewed: object,
    canonical: object,
    path: str = "",
) -> list[str]:
    """Report where a reviewed source projection is absent from canonical data.

    Canonical records can be enriched by another reviewed source adapter. A
    mapping therefore contains the reviewed mapping when every reviewed key is
    present, and a sequence contains the reviewed sequence when its entries
    remain an ordered subsequence. Scalar source values must remain equal.
    """

    if isinstance(reviewed, dict) and isinstance(canonical, dict):
        violations: list[str] = []
        for key, value in reviewed.items():
            child = f"{path}/{key}"
            if key not in canonical:
                violations.append(child)
            else:
                violations.extend(projection_violations(value, canonical[key], child))
        return violations
    if isinstance(reviewed, list) and isinstance(canonical, list):
        canonical_index = 0
        violations = []
        for reviewed_index, value in enumerate(reviewed):
            while canonical_index < len(canonical) and projection_violations(
                value,
                canonical[canonical_index],
            ):
                canonical_index += 1
            if canonical_index == len(canonical):
                violations.append(f"{path}/{reviewed_index}")
            else:
                canonical_index += 1
        return violations
    return [] if reviewed == canonical else [path or "/"]


def canonical_projection_violations(
    reviewed: Mapping[str, Mapping[str, object]],
    canonical: Mapping[str, Mapping[str, object]],
) -> list[str]:
    """Report missing or altered records from one reviewed source projection."""

    violations: list[str] = []
    for pid, record in reviewed.items():
        if pid not in canonical:
            violations.append(f"/{pid}")
        else:
            violations.extend(
                f"/{pid}{path}"
                for path in projection_violations(record, canonical[pid])
            )
    return violations


def export_canonical_json(root: Path, destination: Path) -> dict[str, Path]:
    grouped: dict[str, list[dict[str, object]]] = {}
    records_root = root / "site-specific" / "metadata" / "records"
    for path in sorted(records_root.rglob("*.yaml")):
        if path.parent == records_root and path.name == ".dumpthings.yaml":
            continue
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload.get("pid"):
            raise ZoteroAdapterError(f"Canonical YAML has an invalid record: {path}")
        class_name = path.relative_to(records_root).parts[0]
        grouped.setdefault(class_name, []).append(payload)
    destination.mkdir(parents=True)
    paths: dict[str, Path] = {}
    for class_name, records in sorted(grouped.items()):
        records.sort(key=lambda record: str(record["pid"]))
        path = destination / f"{class_name}.json"
        path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths[class_name] = path
    return paths


def export_noncanonical_mapping_identities(
    review_identities: Path,
    canonical: Mapping[str, Path],
    destination: Path,
) -> dict[str, object]:
    """Make reviewed source identities explicit without promoting site records.

    Creator mappings describe the source identity model. That model can be
    broader than the public site's canonical records. The underlying ingestion
    tool correctly refuses mappings to unknown identities, so the adapter can
    supply a temporary, reportable identity closure derived only from separate,
    reviewed adapter evidence.
    """

    document = yaml.safe_load(review_identities.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("format_version") != 1:
        raise ZoteroAdapterError("Review identities must have format_version: 1")

    canonical_pids: set[str] = set()
    for path in canonical.values():
        payload = load_json(path)
        if not isinstance(payload, list):
            raise ZoteroAdapterError(f"Canonical identity index is invalid: {path}")
        canonical_pids.update(
            str(record["pid"])
            for record in payload
            if isinstance(record, dict) and isinstance(record.get("pid"), str)
        )

    people: list[dict[str, str]] = []
    organizations: list[dict[str, str]] = []
    seen: set[str] = set()
    for mapping in document.get("identities", []):
        if not isinstance(mapping, dict) or not isinstance(mapping.get("pid"), str):
            raise ZoteroAdapterError("Review identities contain an invalid identity")
        pid = str(mapping["pid"])
        if pid in seen:
            raise ZoteroAdapterError(f"Review identity PID is duplicated: {pid}")
        seen.add(pid)
        if pid in canonical_pids:
            raise ZoteroAdapterError(
                f"Review identity is now canonical and must be removed: {pid}"
            )
        aliases = mapping.get("aliases")
        if not isinstance(aliases, list) or not aliases or not isinstance(aliases[0], str):
            raise ZoteroAdapterError(f"Creator mapping has no display alias: {pid}")
        records = [{"pid": pid, "display_label": alias} for alias in aliases]
        if pid.startswith("xyzrins:persons/"):
            people.extend(records)
        elif pid.startswith("xyzrins:organizations/"):
            organizations.extend(records)
        else:
            raise ZoteroAdapterError(
                f"Noncanonical mapping identity has an unsupported PID namespace: {pid}"
            )

    destination.mkdir(parents=True)
    people_path = destination / "XYZPerson.json"
    organizations_path = destination / "XYZOrganization.json"
    people_path.write_text(
        json.dumps(people, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    organizations_path.write_text(
        json.dumps(organizations, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "people_path": people_path,
        "organizations_path": organizations_path,
        "identities": sorted(seen),
    }


def transform_snapshot(
    ingest: ModuleType,
    snapshot: Path,
    output: Path,
    report: Path,
    canonical: Mapping[str, Path],
    creator_map: Path,
    canonical_root: Path,
    noncanonical_identities: Mapping[str, object],
    included_collections: list[str],
    document_collection_classes: Mapping[str, str],
) -> None:
    ingest.command_transform(
        argparse.Namespace(
            creator_map=creator_map,
            existing_data_root=canonical_root,
            input=snapshot,
            organizations=[
                canonical["XYZOrganization"],
                noncanonical_identities["organizations_path"],
            ],
            output_dir=output,
            people=[canonical["XYZPerson"], noncanonical_identities["people_path"]],
            report=report,
            topics=[canonical["XYZTopic"]],
            include_collection=included_collections,
            document_collection=[
                f"{name}={kind}"
                for name, kind in sorted(document_collection_classes.items())
            ],
        )
    )


def export_site_publications(
    site_export: ModuleType,
    publications: Path,
    snapshot: Path,
    policy: Path,
    build_root: Path,
) -> tuple[Path, Path]:
    output = build_root / "zotero-site-publications"
    report = build_root / "zotero-site-publications-report.json"
    site_export.command_export(
        argparse.Namespace(
            output_dir=output,
            policy=policy,
            publications=publications,
            report=report,
            snapshot=snapshot,
        ),
        build_root=build_root,
    )
    return output, report


def source_metadata(snapshot: Mapping[str, object]) -> Mapping[str, object]:
    source = snapshot.get("source")
    if not isinstance(source, dict):
        raise ZoteroAdapterError("Zotero snapshot has no source metadata")
    return source


def queue_counts(report: Mapping[str, object]) -> dict[str, int]:
    return {
        field: len(report.get(field, []))
        for field in REVIEW_QUEUE_FIELDS
        if isinstance(report.get(field, []), list)
    }


def review(context: Mapping[str, object]) -> dict[str, object]:
    from orinoco_metadata_review import semantic_diff

    root = Path(str(context["root"])).resolve()
    output = Path(str(context["output"])).resolve()
    config = context.get("config")
    if not isinstance(config, dict):
        raise ZoteroAdapterError("Zotero adapter config must be a mapping")
    snapshot_path = root / str(config["snapshot"])
    candidates_path = root / str(config["candidates"])
    creator_map = root / str(config["creator_map"])
    review_identities = root / str(config["review_identities"])
    site_policy = root / str(config["site_policy"])
    group_id = int(config["group_id"])
    included_collections = config.get("included_collections")
    document_collection_classes = config.get("document_collection_classes")
    if not isinstance(included_collections, list) or not all(
        isinstance(value, str) and value for value in included_collections
    ):
        raise ZoteroAdapterError(
            "Zotero included_collections must be a non-empty string array"
        )
    if not included_collections:
        raise ZoteroAdapterError("Zotero included_collections cannot be empty")
    if not isinstance(document_collection_classes, dict) or not all(
        isinstance(name, str)
        and name
        and isinstance(kind, str)
        and kind
        for name, kind in document_collection_classes.items()
    ):
        raise ZoteroAdapterError(
            "Zotero document_collection_classes must be a string mapping"
        )
    ingest, site_export = load_tools(root)

    reviewed_snapshot = load_json(snapshot_path)
    if not isinstance(reviewed_snapshot, dict):
        raise ZoteroAdapterError("Reviewed Zotero snapshot is not an object")
    ingest.validate_snapshot(reviewed_snapshot)

    live_snapshot_path = output / "live-snapshot.json"
    ingest.command_fetch(
        argparse.Namespace(
            expected_library_version=None,
            group_id=group_id,
            output=live_snapshot_path,
            snapshot_attempts=3,
            user_agent="orinoco-lite metadata-review/0.1",
        )
    )
    live_snapshot = load_json(live_snapshot_path)
    if not isinstance(live_snapshot, dict):
        raise ZoteroAdapterError("Live Zotero snapshot is not an object")
    ingest.validate_snapshot(live_snapshot)

    canonical_json_root = output / "canonical-json"
    canonical = export_canonical_json(root, canonical_json_root)
    noncanonical_identities = export_noncanonical_mapping_identities(
        review_identities, canonical, output / "noncanonical-mapping-identities"
    )
    reviewed_generated = output / "reviewed-generated-candidates"
    reviewed_transform_report = output / "reviewed-transform-report.json"
    transform_snapshot(
        ingest,
        snapshot_path,
        reviewed_generated,
        reviewed_transform_report,
        canonical,
        creator_map,
        canonical_json_root,
        noncanonical_identities,
        included_collections,
        document_collection_classes,
    )
    reviewed_candidate_diff = semantic_diff(
        candidate_map(candidates_path), candidate_map(reviewed_generated)
    )
    if reviewed_candidate_diff["summary"]["different"]:
        raise ZoteroAdapterError(
            "Committed Zotero candidates are stale relative to the reviewed snapshot"
        )

    live_candidates = output / "live-candidates"
    live_transform_report = output / "live-transform-report.json"
    transform_snapshot(
        ingest,
        live_snapshot_path,
        live_candidates,
        live_transform_report,
        canonical,
        creator_map,
        canonical_json_root,
        noncanonical_identities,
        included_collections,
        document_collection_classes,
    )

    reviewed_site, reviewed_site_report = export_site_publications(
        site_export,
        candidates_path / "XYZPublication.json",
        snapshot_path,
        site_policy,
        output / "reviewed-site-build",
    )
    live_site: Path | None = None
    live_site_report: Path | None = None
    blockers: list[str] = []
    collection_diff = semantic_diff(
        collection_map(reviewed_snapshot), collection_map(live_snapshot)
    )
    policy_error: str | None = None
    try:
        live_site, live_site_report = export_site_publications(
            site_export,
            live_candidates / "XYZPublication.json",
            live_snapshot_path,
            site_policy,
            output / "live-site-build",
        )
    except ValueError as error:
        policy_error = str(error)
        reviewed_collections = collection_map(reviewed_snapshot)
        live_collections = collection_map(live_snapshot)
        collection_changes = [
            f"{change['id']}: {reviewed_collections[change['id']]['name']} -> "
            f"{live_collections[change['id']]['name']}"
            for change in collection_diff["changed"]
        ]
        detail = "; ".join(collection_changes) or "no collection rename was isolated"
        blockers.append(
            "Live Zotero collection policy changed "
            f"({detail}); review source inclusion before refreshing evidence"
        )
        (output / "live-site-policy-error.txt").write_text(
            policy_error + "\n", encoding="utf-8"
        )

    canonical_publications = yaml_map(
        root / "site-specific" / "metadata" / "records" / "XYZPublication"
    )
    # Canonical drift is proposal input, not a source-evidence blocker. The
    # refreshed evidence must reach the default branch before curation can
    # materialize and review the corresponding canonical change.
    reviewed_canonical_violations = canonical_projection_violations(
        yaml_map(reviewed_site), canonical_publications
    )

    reviewed_source = source_metadata(reviewed_snapshot)
    live_source = source_metadata(live_snapshot)
    live_transform = load_json(live_transform_report)
    if not isinstance(live_transform, dict):
        raise ZoteroAdapterError("Live Zotero transform report is not an object")
    source_diff = semantic_diff(snapshot_map(reviewed_snapshot), snapshot_map(live_snapshot))
    candidate_diff = semantic_diff(candidate_map(candidates_path), candidate_map(live_candidates))
    if live_site is None:
        canonical_diff: dict[str, object] = {
            "status": "blocked",
            "reason": blockers[0],
            "summary": {
                "added": 0,
                "removed": 0,
                "changed": 0,
                "unchanged": 0,
                "different": True,
                "blocked": True,
            },
            "added": [],
            "removed": [],
            "changed": [],
        }
    else:
        canonical_diff = semantic_diff(canonical_publications, yaml_map(live_site))

    updates: list[dict[str, object]] = [
        {
            "operation": "replace",
            "staged": relative(root, live_snapshot_path),
            "destination": str(config["snapshot"]),
        }
    ]
    for class_name in TARGET_CLASSES:
        staged = live_candidates / f"{class_name}.json"
        destination = Path(str(config["candidates"])) / f"{class_name}.json"
        records = load_json(staged)
        if records:
            updates.append(
                {
                    "operation": "replace",
                    "staged": relative(root, staged),
                    "destination": destination.as_posix(),
                }
            )
        elif (root / destination).exists():
            updates.append(
                {"operation": "delete", "destination": destination.as_posix()}
            )

    return {
        "adapter_api_version": ADAPTER_API_VERSION,
        "source_id": "zotero",
        "canonical_promotion": False,
        "source": {
            "kind": "zotero-public-library",
            "group_id": group_id,
            "reviewed_version": reviewed_source.get("library_version"),
            "live_version": live_source.get("library_version"),
            "reviewed_sha256": reviewed_source.get("content_sha256"),
            "live_sha256": live_source.get("content_sha256"),
            "collection_diff": collection_diff,
            **({"site_policy_error": policy_error} if policy_error else {}),
        },
        "source_diff": source_diff,
        "candidate_diff": candidate_diff,
        "canonical_diff": canonical_diff,
        "blockers": blockers,
        "review_queues": queue_counts(live_transform),
        "noncanonical_mapping_targets": noncanonical_identities["identities"],
        "baseline": {
            "reviewed_candidates_current": True,
            "canonical_publications_current": not reviewed_canonical_violations,
        },
        "artifacts": {
            "live_snapshot": relative(root, live_snapshot_path),
            "live_candidates": relative(root, live_candidates),
            "live_transform_report": relative(root, live_transform_report),
            **(
                {
                    "live_site_policy_error": relative(
                        root, output / "live-site-policy-error.txt"
                    )
                }
                if policy_error
                else {}
            ),
            **(
                {
                    "live_site_candidates": relative(root, live_site),
                    "live_site_report": relative(root, live_site_report),
                }
                if live_site is not None and live_site_report is not None
                else {}
            ),
        },
        "evidence_updates": updates,
    }
