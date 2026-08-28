#!/usr/bin/env python3
"""Validate and map the reviewed ``dump-research-info`` source roots.

This module owns only source-specific acquisition checks, identity matching,
and transformation policy. Candidate construction, canonical serialization,
annotation companions, decisions, and finalization are shared engine
contracts.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Mapping

import yaml


DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
FULL_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
RELATION_KEYS = {"associated_with", "attributed_to", "generated_by", "part_of"}
RELATION_SCALAR_FIELDS = {"at_location", "creator"}
RELATION_LIST_FIELDS = {"about", "part_of"}
CLASS_NAME = re.compile(r"XYZ[A-Za-z0-9]+\Z")
SAFE_STEM = re.compile(r"[^a-z0-9]+")
PRIMARY_SOURCE_DIRECTORY = "data/con_site"
ROLE_SOURCE_DIRECTORY = "data/pool_psychoinformatics_de"
ROLE_CLASS = "XYZAgentRole"


class DumpResearchInfoAdapterError(RuntimeError):
    """Report malformed source, downstream metadata, or source coordinates."""


@dataclass(frozen=True)
class SourceTarget:
    """One validated source record and its deterministic canonical target."""

    source_class: str
    source_pid: str
    source_directory: str
    target_pid: str
    record_path: str
    source_record: Mapping[str, object]
    transformed_record: Mapping[str, object]
    baseline_record: Mapping[str, object] | None

    @property
    def source_record_id(self) -> str:
        identity = f"{self.source_class}:{self.source_pid}"
        if self.source_directory == PRIMARY_SOURCE_DIRECTORY:
            return identity
        return f"{self.source_directory}:{identity}"


def git_output(path: Path, *arguments: str) -> str:
    """Return one Git query result or a source-specific diagnostic."""

    try:
        return subprocess.check_output(
            ["git", "-C", str(path), *arguments],
            stderr=subprocess.PIPE,
            text=True,
        ).strip()
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "git command failed"
        raise DumpResearchInfoAdapterError(
            f"Cannot inspect Git checkout {path}: {detail}"
        ) from error


def git_commit(path: Path) -> str:
    return git_output(path, "rev-parse", "HEAD^{commit}")


def git_tree(path: Path, relative: str) -> str:
    return git_output(path, "rev-parse", f"HEAD:{relative}")


def git_dirty(path: Path) -> bool:
    return bool(git_output(path, "status", "--porcelain=v1", "--untracked-files=all"))


def exact_commit(value: object, *, label: str) -> str:
    if not isinstance(value, str) or FULL_COMMIT.fullmatch(value) is None:
        raise DumpResearchInfoAdapterError(
            f"{label} must be an exact lowercase 40-hex Git commit"
        )
    return value


def source_directory_path(value: object) -> str:
    """Validate the repository-relative directory included in the coordinate."""

    if not isinstance(value, str) or not value:
        raise DumpResearchInfoAdapterError(
            "Source directory must be a non-empty repository-relative path"
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DumpResearchInfoAdapterError(
            "Source directory must be a normalized repository-relative POSIX path"
        )
    return value


def validate_source_checkout(
    source_checkout: Path,
    *,
    expected_commit: str,
    source_directory: str,
) -> tuple[Path, str]:
    """Bind one ordinary clean source repository to its commit and tree."""

    expected = exact_commit(expected_commit, label="Expected source commit")
    relative = source_directory_path(source_directory)
    checkout = source_checkout.resolve()
    if (
        not checkout.is_dir()
        or source_checkout.is_symlink()
        or Path(git_output(checkout, "rev-parse", "--show-toplevel")).resolve()
        != checkout
    ):
        raise DumpResearchInfoAdapterError(
            "Source checkout must identify one ordinary Git repository root"
        )
    observed = git_commit(checkout)
    if observed != expected:
        raise DumpResearchInfoAdapterError(
            f"Source checkout moved: expected {expected}, found {observed}"
        )
    if git_dirty(checkout):
        raise DumpResearchInfoAdapterError(
            "Source checkout must be clean, including untracked files"
        )
    source_root = checkout.joinpath(*PurePosixPath(relative).parts)
    if not source_root.is_dir() or source_root.is_symlink():
        raise DumpResearchInfoAdapterError(
            f"Source directory is not an ordinary directory: {relative}"
        )
    return checkout, git_tree(checkout, relative)


def load_source(root: Path) -> dict[str, dict[str, dict[str, object]]]:
    """Load the reviewed legacy class-array representation."""

    result: dict[str, dict[str, dict[str, object]]] = {}
    seen: set[str] = set()
    paths = sorted(root.glob("XYZ*.json"))
    if not paths:
        raise DumpResearchInfoAdapterError(
            f"No XYZ class files exist in source directory {root}"
        )
    for path in paths:
        if (
            path.is_symlink()
            or not path.is_file()
            or CLASS_NAME.fullmatch(path.stem) is None
        ):
            raise DumpResearchInfoAdapterError(
                f"Source class path is not an ordinary XYZ JSON file: {path}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DumpResearchInfoAdapterError(
                f"Cannot read source class {path}: {error}"
            ) from error
        if not isinstance(payload, list):
            raise DumpResearchInfoAdapterError(f"Source class is not a list: {path}")
        records: dict[str, dict[str, object]] = {}
        for record in payload:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("pid"), str)
                or not record["pid"]
            ):
                raise DumpResearchInfoAdapterError(f"Source record has no PID: {path}")
            pid = str(record["pid"])
            if pid in seen:
                raise DumpResearchInfoAdapterError(f"Source PID is duplicated: {pid}")
            seen.add(pid)
            records[pid] = record
        result[path.stem] = records
    return result


def referenced_role_pids(value: object) -> set[str]:
    """Return every role PID from the reviewed nested relationship shapes."""

    result: set[str] = set()
    if isinstance(value, dict):
        for field, child in value.items():
            if field == "roles":
                if not isinstance(child, list) or not all(
                    isinstance(role, str) and role for role in child
                ):
                    raise DumpResearchInfoAdapterError(
                        "Source relationship roles must be non-empty PID strings"
                    )
                result.update(child)
            else:
                result.update(referenced_role_pids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(referenced_role_pids(child))
    return result


def source_records_with_role_dependencies(
    primary: Mapping[str, Mapping[str, Mapping[str, object]]],
    role_pool: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> tuple[
    dict[str, dict[str, dict[str, object]]],
    dict[tuple[str, str], str],
]:
    """Assemble one PID-coalesced source view with authoritative pool roles."""

    records = {
        class_name: {
            pid: deepcopy(dict(record)) for pid, record in source_records.items()
        }
        for class_name, source_records in primary.items()
        if class_name != ROLE_CLASS
    }
    directories = {
        (class_name, pid): PRIMARY_SOURCE_DIRECTORY
        for class_name, source_records in records.items()
        for pid in source_records
    }
    required_roles = {
        role
        for source_records in primary.values()
        for record in source_records.values()
        for role in referenced_role_pids(record)
    }
    pool_roles = role_pool.get(ROLE_CLASS, {})
    primary_by_pid = {
        pid: (class_name, record)
        for class_name, source_records in primary.items()
        for pid, record in source_records.items()
    }
    pool_by_pid = {
        pid: (class_name, record)
        for class_name, source_records in role_pool.items()
        for pid, record in source_records.items()
    }

    for pid in sorted(set(primary_by_pid) & set(pool_by_pid)):
        primary_class, primary_record = primary_by_pid[pid]
        pool_class, pool_record = pool_by_pid[pid]
        if primary_class != pool_class:
            raise DumpResearchInfoAdapterError(
                f"Source PID {pid} is a {primary_class} in "
                f"{PRIMARY_SOURCE_DIRECTORY}, but a {pool_class} in "
                f"{ROLE_SOURCE_DIRECTORY}"
            )
        if dict(primary_record) != dict(pool_record):
            raise DumpResearchInfoAdapterError(
                f"Source PID {pid} has conflicting records in "
                f"{PRIMARY_SOURCE_DIRECTORY} and {ROLE_SOURCE_DIRECTORY}"
            )

    for pid in sorted(required_roles):
        pool_record = pool_roles.get(pid)
        if pool_record is None:
            raise DumpResearchInfoAdapterError(
                f"Required role target {pid} has no authoritative {ROLE_CLASS} "
                f"record in {ROLE_SOURCE_DIRECTORY}"
            )
        records.setdefault(ROLE_CLASS, {})[pid] = deepcopy(dict(pool_record))
        directories[(ROLE_CLASS, pid)] = ROLE_SOURCE_DIRECTORY

    return records, directories


def load_yaml_records(downstream_root: Path) -> dict[str, dict[str, object]]:
    """Load canonical records with their stable class and relative path."""

    result: dict[str, dict[str, object]] = {}
    records_root = downstream_root / "site-specific/metadata/records"
    if not records_root.is_dir() or records_root.is_symlink():
        raise DumpResearchInfoAdapterError(
            f"Downstream metadata root is not an ordinary directory: {records_root}"
        )
    for path in sorted(records_root.rglob("*.yaml")):
        if path.name.startswith("."):
            continue
        if path.is_symlink() or not path.is_file():
            raise DumpResearchInfoAdapterError(
                f"Downstream record is not an ordinary file: {path}"
            )
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise DumpResearchInfoAdapterError(
                f"Cannot read downstream record {path}: {error}"
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("pid"), str):
            raise DumpResearchInfoAdapterError(f"Downstream record has no PID: {path}")
        pid = str(payload["pid"])
        if pid in result:
            raise DumpResearchInfoAdapterError(f"Downstream PID is duplicated: {pid}")
        result[pid] = {
            "class": path.parent.name,
            "path": path.relative_to(records_root).as_posix(),
            "record": payload,
        }
    return result


def normalized_doi(value: object) -> str | None:
    text = str(value).strip().lower().rstrip("/")
    if "doi.org/" in text:
        text = text.split("doi.org/", 1)[1]
    if text.startswith("doi:"):
        text = text[4:]
    return text if DOI.fullmatch(text) else None


def identity_tokens(record: Mapping[str, object]) -> set[str]:
    """Return exact PID/identifier and normalized DOI match tokens."""

    values = [record.get("pid")]
    identifiers = record.get("identifiers", [])
    if isinstance(identifiers, list):
        values.extend(
            identifier.get("notation")
            for identifier in identifiers
            if isinstance(identifier, dict)
        )
    tokens: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        tokens.add(f"value:{text.casefold().rstrip('/')}")
        if doi := normalized_doi(text):
            tokens.add(f"doi:{doi}")
    return tokens


def match_record(
    source_class: str,
    source: Mapping[str, object],
    downstream: Mapping[str, Mapping[str, object]],
    token_index: Mapping[str, set[str]],
) -> tuple[str | None, str, tuple[str, ...]]:
    """Apply the reviewed exact-PID then unique same-class identifier rule."""

    pid = str(source["pid"])
    if pid in downstream:
        downstream_class = downstream[pid].get("class")
        if downstream_class != source_class:
            raise DumpResearchInfoAdapterError(
                f"Source record {pid} is in {source_class}, but the exact "
                f"downstream PID is in {downstream_class}"
            )
        return pid, "exact-pid", (pid,)
    matches: set[str] = set()
    for token in identity_tokens(source):
        matches.update(token_index.get(token, set()))
    same_class = {
        candidate
        for candidate in matches
        if downstream[candidate]["class"] == source_class
    }
    if len(same_class) == 1:
        return next(iter(same_class)), "identifier", tuple(sorted(same_class))
    if same_class:
        return None, "ambiguous-identifier", tuple(sorted(same_class))
    return None, "unmatched", ()


def rewrite_relation_targets(
    value: object,
    pid_map: Mapping[str, str],
    field: str | None = None,
) -> object:
    """Retarget only the reviewed relation shapes to matched canonical PIDs."""

    if field in RELATION_SCALAR_FIELDS and isinstance(value, str):
        return pid_map.get(value, value)
    if field in RELATION_LIST_FIELDS and isinstance(value, list):
        return [
            pid_map.get(child, child) if isinstance(child, str) else deepcopy(child)
            for child in value
        ]
    if isinstance(value, dict):
        result: dict[str, object] = dict(value)
        if field in RELATION_KEYS and isinstance(value.get("object"), str):
            target = str(value["object"])
            result["object"] = pid_map.get(target, target)
        for key, child in value.items():
            if key == "object" and field in RELATION_KEYS:
                continue
            result[key] = rewrite_relation_targets(child, pid_map, key)
        return result
    if isinstance(value, list):
        return [rewrite_relation_targets(child, pid_map, field) for child in value]
    return deepcopy(value)


def canonical_source_pid(class_name: str, source_pid: str) -> str:
    if source_pid.startswith("xyzrins:"):
        return source_pid
    namespaces = {
        "XYZPublication": "publications",
        "XYZPublicationVenue": "publication-venues",
    }
    namespace = namespaces.get(class_name)
    if namespace is None:
        return source_pid
    return f"xyzrins:{namespace}/{record_stem(source_pid)}"


def native_record(class_name: str, source: Mapping[str, object]) -> dict[str, object]:
    if CLASS_NAME.fullmatch(class_name) is None:
        raise DumpResearchInfoAdapterError(
            f"Source class cannot become a canonical record directory: {class_name}"
        )
    expected_type = f"xyzri:{class_name}"
    observed_type = source.get("schema_type")
    if observed_type not in (None, expected_type):
        raise DumpResearchInfoAdapterError(
            f"Source record {source.get('pid')} has unexpected schema_type "
            f"{observed_type!r}; expected {expected_type}"
        )
    source_pid = str(source["pid"])
    target_pid = canonical_source_pid(class_name, source_pid)
    result = {
        "pid": target_pid,
        "schema_type": expected_type,
        **{
            key: deepcopy(value)
            for key, value in source.items()
            if key not in {"pid", "schema_type"}
        },
    }
    if target_pid != source_pid:
        identifiers = result.setdefault("identifiers", [])
        if not isinstance(identifiers, list):
            raise DumpResearchInfoAdapterError(
                f"Source record {source_pid} identifiers are not a list"
            )
        if not any(
            isinstance(identifier, dict) and identifier.get("notation") == source_pid
            for identifier in identifiers
        ):
            identifiers.append(
                {
                    "notation": source_pid,
                    "schema_type": "dlthings:Identifier",
                }
            )
    return result


def transformed_source_record(
    class_name: str,
    source: Mapping[str, object],
    target_pid: str,
    pid_map: Mapping[str, str],
) -> dict[str, object]:
    record = native_record(class_name, source)
    record["pid"] = target_pid
    rewritten = rewrite_relation_targets(record, pid_map)
    if not isinstance(rewritten, dict):  # pragma: no cover - record is a mapping
        raise AssertionError("Transformed source record is not a mapping")
    return rewritten


def record_stem(pid: str) -> str:
    if doi := normalized_doi(pid):
        source = f"doi-{doi}"
    elif pid.casefold().startswith("issn:"):
        source = f"issn-{pid.split(':', 1)[1]}"
    elif pid.startswith("xyzrins:") and "/" in pid:
        source = pid.rsplit("/", 1)[1]
    elif pid.casefold().startswith("ror:"):
        source = f"ror-{pid.split(':', 1)[1]}"
    else:
        source = pid
    stem = SAFE_STEM.sub("-", source.casefold()).strip("-")
    if not stem:
        raise DumpResearchInfoAdapterError(
            f"Source PID cannot become a canonical filename: {pid}"
        )
    return stem


def build_source_targets(
    source_checkout: Path,
    downstream_root: Path,
    *,
    expected_source_commit: str,
) -> tuple[tuple[SourceTarget, ...], dict[str, object]]:
    """Return primary upserts plus their authoritative role dependencies."""

    root = downstream_root.resolve()
    checkout, primary_tree = validate_source_checkout(
        source_checkout,
        expected_commit=expected_source_commit,
        source_directory=PRIMARY_SOURCE_DIRECTORY,
    )
    _, role_tree = validate_source_checkout(
        source_checkout,
        expected_commit=expected_source_commit,
        source_directory=ROLE_SOURCE_DIRECTORY,
    )
    primary_records = load_source(
        checkout.joinpath(*PurePosixPath(PRIMARY_SOURCE_DIRECTORY).parts)
    )
    role_pool = load_source(
        checkout.joinpath(*PurePosixPath(ROLE_SOURCE_DIRECTORY).parts)
    )
    downstream = load_yaml_records(root)
    source_records, source_directories = source_records_with_role_dependencies(
        primary_records,
        role_pool,
    )

    token_index: dict[str, set[str]] = {}
    for pid, entry in downstream.items():
        record = entry["record"]
        if not isinstance(record, dict):  # pragma: no cover - loader invariant
            raise AssertionError("Downstream record is not a mapping")
        for token in identity_tokens(record):
            token_index.setdefault(token, set()).add(pid)

    matches: dict[tuple[str, str], str] = {}
    for class_name, records in sorted(source_records.items()):
        for source_pid, source_record in sorted(records.items()):
            if source_directories[(class_name, source_pid)] == ROLE_SOURCE_DIRECTORY:
                if source_pid in downstream:
                    downstream_class = downstream[source_pid].get("class")
                    if downstream_class != ROLE_CLASS:
                        raise DumpResearchInfoAdapterError(
                            f"Authoritative role {source_pid} is in {ROLE_CLASS}, "
                            f"but its exact downstream PID is in {downstream_class}"
                        )
                    target_pid, method, candidates = (
                        source_pid,
                        "exact-pid",
                        (source_pid,),
                    )
                else:
                    target_pid, method, candidates = None, "unmatched", ()
            else:
                target_pid, method, candidates = match_record(
                    class_name, source_record, downstream, token_index
                )
            if method == "ambiguous-identifier":
                raise DumpResearchInfoAdapterError(
                    f"Source record {class_name}:{source_pid} ambiguously matches "
                    + ", ".join(candidates)
                )
            if target_pid is not None:
                matches[(class_name, source_pid)] = target_pid

    pid_map = {
        source_pid: matches.get(
            (class_name, source_pid), canonical_source_pid(class_name, source_pid)
        )
        for class_name, records in sorted(source_records.items())
        for source_pid in sorted(records)
    }

    targets: list[SourceTarget] = []
    planned_pids: dict[str, str] = {}
    planned_paths: dict[str, str] = {}
    for class_name, records in sorted(source_records.items()):
        for source_pid, source_record in sorted(records.items()):
            source_identity = f"{class_name}:{source_pid}"
            target_pid = pid_map[source_pid]
            baseline: Mapping[str, object] | None = None
            if (matched_pid := matches.get((class_name, source_pid))) is not None:
                entry = downstream[matched_pid]
                record_path = str(entry["path"])
                record = entry["record"]
                if not isinstance(record, Mapping):  # pragma: no cover
                    raise AssertionError("Downstream record is not a mapping")
                baseline = record
            else:
                if target_pid in downstream:
                    raise DumpResearchInfoAdapterError(
                        f"Source record {source_identity} resolves to existing "
                        f"downstream PID {target_pid} without matching it"
                    )
                record_path = f"{class_name}/{record_stem(source_pid)}.yaml"
                target = root / "site-specific/metadata/records" / PurePosixPath(record_path)
                if target.exists() or target.is_symlink():
                    raise DumpResearchInfoAdapterError(
                        f"Source-only record target already exists: {record_path}"
                    )

            if previous := planned_pids.get(target_pid):
                raise DumpResearchInfoAdapterError(
                    f"Source records {previous} and {source_identity} resolve to "
                    f"the same final PID {target_pid}"
                )
            if previous := planned_paths.get(record_path):
                raise DumpResearchInfoAdapterError(
                    f"Source records {previous} and {source_identity} resolve to "
                    f"the same final path {record_path}"
                )
            planned_pids[target_pid] = source_identity
            planned_paths[record_path] = source_identity
            transformed = transformed_source_record(
                class_name, source_record, target_pid, pid_map
            )
            targets.append(
                SourceTarget(
                    source_class=class_name,
                    source_pid=source_pid,
                    source_directory=source_directories[(class_name, source_pid)],
                    target_pid=target_pid,
                    record_path=record_path,
                    source_record=deepcopy(source_record),
                    transformed_record=transformed,
                    baseline_record=deepcopy(baseline),
                )
            )

    if git_commit(checkout) != expected_source_commit or git_dirty(checkout):
        raise DumpResearchInfoAdapterError(
            "Source checkout changed while source targets were being built"
        )
    coordinate = {
        "repository": "https://github.com/con/dump-research-info",
        "commit": expected_source_commit,
        "source_roots": {
            PRIMARY_SOURCE_DIRECTORY: primary_tree,
            ROLE_SOURCE_DIRECTORY: role_tree,
        },
    }
    return tuple(targets), coordinate


__all__ = [
    "DumpResearchInfoAdapterError",
    "SourceTarget",
    "build_source_targets",
    "canonical_source_pid",
    "identity_tokens",
    "load_source",
    "load_yaml_records",
    "match_record",
    "native_record",
    "normalized_doi",
    "record_stem",
    "referenced_role_pids",
    "rewrite_relation_targets",
    "source_records_with_role_dependencies",
    "transformed_source_record",
    "validate_source_checkout",
]
