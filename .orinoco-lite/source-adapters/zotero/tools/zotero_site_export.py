#!/usr/bin/env python3
"""Render reviewed Zotero publications as upstream-compatible site YAML.

The output is a migration candidate, not a website runtime dependency. The
website repository remains the authority after a reviewer copies and commits
the candidate records.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

import yaml

from zotero_ingest import validate_snapshot


DOI_SLUG = re.compile(r"[^a-z0-9]+")
ZOTERO_IDENTIFIER = re.compile(r"^zotero:group:(\d+):item:([A-Z0-9]+)$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
BUILD_ROOT = REPOSITORY_ROOT / "build"
OUTPUT_RELATIVE_PATH = Path("zotero-site-publications")
REPORT_RELATIVE_PATH = Path("zotero-site-publications-report.json")
POLICY_FIELDS = {
    "allowed_about_targets",
    "allowed_attribution_targets",
    "allowed_curated_generation_targets",
    "curated_generations",
    "description",
    "format_version",
    "omitted_attribution_targets",
    "omitted_generation_objects",
    "pid_overrides",
}
POLICY_USAGE_FIELDS = POLICY_FIELDS - {"description", "format_version"} | {
    "curated_generation_entries"
}
CURATED_GENERATION_FIELDS = {
    "at_location",
    "at_time",
    "object",
    "rationale",
}


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects silently overwritten mapping keys."""


def construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"Duplicate YAML mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


def read_input_bytes(path: Path) -> bytes:
    return path.read_bytes()


def parse_policy(payload: bytes) -> dict[str, Any]:
    value = yaml.load(payload.decode("utf-8"), Loader=UniqueKeyLoader)
    if not isinstance(value, dict) or value.get("format_version") != 1:
        raise ValueError("Site migration policy must use format_version: 1")
    missing = sorted(POLICY_FIELDS - set(value))
    unknown = sorted(set(value) - POLICY_FIELDS)
    if missing or unknown:
        raise ValueError(
            "Site migration policy fields do not match the contract: "
            f"missing={missing}, unknown={unknown}"
        )
    if not isinstance(value["description"], str) or not value["description"].strip():
        raise ValueError("description must be a non-empty string")
    policy_mapping(value["pid_overrides"], "pid_overrides")
    unique_strings(
        value["allowed_attribution_targets"], "allowed_attribution_targets"
    )
    policy_mapping(
        value["omitted_attribution_targets"], "omitted_attribution_targets"
    )
    unique_strings(value["allowed_about_targets"], "allowed_about_targets")
    policy_mapping(
        value["omitted_generation_objects"], "omitted_generation_objects"
    )
    unique_strings(
        value["allowed_curated_generation_targets"],
        "allowed_curated_generation_targets",
    )
    overlapping_people = sorted(
        set(value["allowed_attribution_targets"])
        & set(value["omitted_attribution_targets"])
    )
    if overlapping_people:
        raise ValueError(
            "Attribution targets cannot be both allowed and omitted: "
            f"{overlapping_people}"
        )
    curated = value["curated_generations"]
    if not isinstance(curated, dict) or not all(
        isinstance(source_pid, str)
        and source_pid
        and isinstance(generations, list)
        and generations
        for source_pid, generations in curated.items()
    ):
        raise ValueError(
            "curated_generations must map non-empty source PIDs to non-empty lists"
        )
    for source_pid, generations in curated.items():
        seen: set[str] = set()
        for index, generation in enumerate(generations):
            if not isinstance(generation, dict):
                raise ValueError(
                    f"curated_generations {source_pid}[{index}] must be a mapping"
                )
            missing_generation = sorted({"object", "rationale"} - set(generation))
            unknown_generation = sorted(set(generation) - CURATED_GENERATION_FIELDS)
            if missing_generation or unknown_generation:
                raise ValueError(
                    f"curated_generations {source_pid}[{index}] fields do not match "
                    "the contract: "
                    f"missing={missing_generation}, unknown={unknown_generation}"
                )
            if not all(
                isinstance(value, str) and value.strip()
                for value in generation.values()
            ):
                raise ValueError(
                    f"curated_generations {source_pid}[{index}] values must be "
                    "non-empty strings"
                )
            fingerprint = curated_generation_fingerprint(source_pid, generation)
            if fingerprint in seen:
                raise ValueError(
                    f"curated_generations {source_pid} contains duplicate entries"
                )
            seen.add(fingerprint)
    return value


def load_policy(path: Path) -> dict[str, Any]:
    return parse_policy(read_input_bytes(path))


def unique_strings(value: Any, label: str) -> set[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{label} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} contains duplicate values")
    return set(value)


def policy_mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and key and isinstance(item, str) and item.strip()
        for key, item in value.items()
    ):
        raise ValueError(f"{label} must map non-empty strings to rationales")
    return value


def curated_generation_fingerprint(
    source_pid: str, generation: dict[str, Any]
) -> str:
    semantic_value = {
        key: value for key, value in generation.items() if key != "rationale"
    }
    return f"{source_pid}:{json.dumps(semantic_value, sort_keys=True)}"


def identifier_values(record: dict[str, Any], schema_type: str) -> list[str]:
    return [
        str(identifier["notation"])
        for identifier in record.get("identifiers", [])
        if isinstance(identifier, dict)
        and identifier.get("schema_type") == schema_type
        and identifier.get("notation")
    ]


def site_pid(record: dict[str, Any], overrides: dict[str, str]) -> str:
    source_pid = str(record.get("pid", ""))
    if source_pid in overrides:
        return overrides[source_pid]
    dois = identifier_values(record, "dlthings:DOI")
    if len(dois) == 1:
        slug = DOI_SLUG.sub("-", dois[0].casefold()).strip("-")
        return f"xyzrins:publications/doi-{slug}"
    zotero = identifier_values(record, "dlthings:Identifier")
    keys = sorted(
        match.group(2).casefold()
        for value in zotero
        if (match := ZOTERO_IDENTIFIER.fullmatch(value))
    )
    if not keys:
        raise ValueError(f"{source_pid}: no stable DOI or Zotero item identifier")
    return f"xyzrins:publications/zotero-{keys[0]}"


def add_schema_types(values: Any, schema_type: str) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError(f"Expected a list of {schema_type} values")
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(f"Expected a mapping in {schema_type} values")
        typed = dict(value)
        typed["schema_type"] = schema_type
        result.append(typed)
    return result


def render_publication(
    record: dict[str, Any],
    policy: dict[str, Any],
    counters: Counter[str],
    policy_usage: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    usage = policy_usage if policy_usage is not None else empty_policy_usage()
    source_pid = str(record.get("pid", ""))
    if not source_pid or not record.get("title"):
        raise ValueError("Every source publication must define pid and title")
    overrides = policy.get("pid_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("pid_overrides must be a mapping")
    allowed_people = unique_strings(
        policy.get("allowed_attribution_targets"), "allowed_attribution_targets"
    )
    omitted_people = policy_mapping(
        policy.get("omitted_attribution_targets"), "omitted_attribution_targets"
    )
    allowed_topics = unique_strings(
        policy.get("allowed_about_targets"), "allowed_about_targets"
    )
    omitted_generation = policy_mapping(
        policy.get("omitted_generation_objects"), "omitted_generation_objects"
    )
    allowed_curated_generation = unique_strings(
        policy.get("allowed_curated_generation_targets"),
        "allowed_curated_generation_targets",
    )
    curated_generations = policy.get("curated_generations")
    if not isinstance(curated_generations, dict):
        raise ValueError("curated_generations must be a mapping")

    output: dict[str, Any] = {
        "pid": site_pid(record, overrides),
        "schema_type": "xyzri:XYZPublication",
        "title": record["title"],
        "display_label": record.get("display_label", record["title"]),
    }
    if source_pid in overrides:
        usage["pid_overrides"].add(source_pid)
    for field in ("description", "kind"):
        if record.get(field):
            output[field] = record[field]
    if identifiers := record.get("identifiers"):
        output["identifiers"] = identifiers

    attributions: list[dict[str, Any]] = []
    for attribution in record.get("attributed_to", []):
        if not isinstance(attribution, dict) or not attribution.get("object"):
            raise ValueError(f"{source_pid}: malformed attribution")
        target = str(attribution["object"])
        if target in allowed_people:
            usage["allowed_attribution_targets"].add(target)
            typed = dict(attribution)
            typed["schema_type"] = "dlthings:Attribution"
            attributions.append(typed)
        elif target in omitted_people:
            usage["omitted_attribution_targets"].add(target)
            counters[f"omitted attribution {target}"] += 1
        else:
            raise ValueError(f"{source_pid}: unreviewed attribution target {target}")
    if attributions:
        output["attributed_to"] = attributions

    about = record.get("about", [])
    if not isinstance(about, list):
        raise ValueError(f"{source_pid}: about must be a list")
    unknown_topics = sorted(set(about) - allowed_topics)
    if unknown_topics:
        raise ValueError(f"{source_pid}: unreviewed topic targets {unknown_topics}")
    if about:
        usage["allowed_about_targets"].update(str(target) for target in about)
        output["about"] = about

    if attributes := record.get("attributes"):
        output["attributes"] = add_schema_types(
            attributes, "dlthings:AttributeSpecification"
        )
    for generation in record.get("generated_by", []):
        if not isinstance(generation, dict) or not generation.get("object"):
            raise ValueError(f"{source_pid}: malformed generation")
        target = str(generation["object"])
        if target not in omitted_generation:
            raise ValueError(f"{source_pid}: unreviewed generation target {target}")
        usage["omitted_generation_objects"].add(target)
        counters[f"omitted generation {target}"] += 1
    curated = curated_generations.get(source_pid, [])
    if not isinstance(curated, list):
        raise ValueError(f"{source_pid}: curated_generations must be a list")
    retained: list[dict[str, Any]] = []
    if source_pid in curated_generations:
        usage["curated_generations"].add(source_pid)
    for generation in curated:
        if not isinstance(generation, dict) or not generation.get("object"):
            raise ValueError(f"{source_pid}: malformed curated generation")
        target = str(generation["object"])
        if target not in allowed_curated_generation:
            raise ValueError(
                f"{source_pid}: unreviewed curated generation target {target}"
            )
        usage["allowed_curated_generation_targets"].add(target)
        usage["curated_generation_entries"].add(
            curated_generation_fingerprint(source_pid, generation)
        )
        typed = {key: value for key, value in generation.items() if key != "rationale"}
        typed["schema_type"] = "dlthings:Generation"
        retained.append(typed)
    if retained:
        output["generated_by"] = retained
    return output


def yaml_bytes(record: dict[str, Any]) -> bytes:
    return yaml.safe_dump(
        record,
        allow_unicode=True,
        sort_keys=False,
        width=88,
    ).encode("utf-8")


def directory_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, payload in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def empty_policy_usage() -> dict[str, set[str]]:
    return {field: set() for field in POLICY_USAGE_FIELDS}


def declared_policy_entries(policy: dict[str, Any]) -> dict[str, set[str]]:
    return {
        "pid_overrides": set(policy["pid_overrides"]),
        "allowed_attribution_targets": set(policy["allowed_attribution_targets"]),
        "omitted_attribution_targets": set(policy["omitted_attribution_targets"]),
        "allowed_about_targets": set(policy["allowed_about_targets"]),
        "omitted_generation_objects": set(policy["omitted_generation_objects"]),
        "allowed_curated_generation_targets": set(
            policy["allowed_curated_generation_targets"]
        ),
        "curated_generations": set(policy["curated_generations"]),
        "curated_generation_entries": {
            curated_generation_fingerprint(source_pid, generation)
            for source_pid, generations in policy["curated_generations"].items()
            for generation in generations
        },
    }


def require_exact_policy_usage(
    policy: dict[str, Any], usage: dict[str, set[str]]
) -> None:
    unused = {
        field: sorted(values - usage[field])
        for field, values in declared_policy_entries(policy).items()
        if values - usage[field]
    }
    if unused:
        details = "; ".join(
            f"{field}={values}" for field, values in sorted(unused.items())
        )
        raise ValueError(f"Unused site migration policy entries: {details}")


def absolute_path(path: Path) -> Path:
    """Normalize a CLI path without following a possibly unsafe symlink."""
    return Path(os.path.abspath(os.fspath(path)))


def is_same_or_descendant(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def reject_symlink_components(path: Path, label: str, build_root: Path) -> None:
    relative = path.relative_to(build_root)
    current = build_root
    for part in ("", *relative.parts):
        if part:
            current /= part
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink component: {current}")
        if not current.exists():
            break


def reject_descendant_symlinks(path: Path, label: str) -> None:
    if not path.exists() or not path.is_dir():
        return
    for parent, directories, filenames in os.walk(path, followlinks=False):
        parent_path = Path(parent)
        for name in (*directories, *filenames):
            candidate = parent_path / name
            if candidate.is_symlink():
                raise ValueError(
                    f"{label} contains a symlink descendant: {candidate}"
                )


def build_destination(path: Path, label: str, build_root: Path) -> Path:
    destination = absolute_path(path)
    if destination == build_root or build_root not in destination.parents:
        raise ValueError(
            f"{label} must be a strict descendant of repository build state "
            f"({build_root})"
        )
    reject_symlink_components(destination, label, build_root)
    return destination


def validate_export_destinations(
    output_dir: Path,
    report: Path,
    inputs: list[Path],
    build_root: Path,
) -> tuple[Path, Path]:
    normalized_build_root = absolute_path(build_root)
    output = build_destination(
        output_dir, "output directory", normalized_build_root
    )
    report_path = build_destination(report, "report", normalized_build_root)
    if is_same_or_descendant(report_path, output) or is_same_or_descendant(
        output, report_path
    ):
        raise ValueError("Output directory and report path must not overlap")
    expected_output = normalized_build_root / OUTPUT_RELATIVE_PATH
    expected_report = normalized_build_root / REPORT_RELATIVE_PATH
    if output != expected_output or report_path != expected_report:
        raise ValueError(
            "Site export destinations must be the dedicated build artifacts: "
            f"output={expected_output}, report={expected_report}"
        )
    if output.exists() and not output.is_dir():
        raise ValueError(f"Output directory is not a directory: {output}")
    reject_descendant_symlinks(output, "output directory")
    if output.exists():
        unexpected = sorted(
            path.name
            for path in output.iterdir()
            if not path.is_file() or path.suffix != ".yaml"
        )
        if unexpected:
            raise ValueError(
                "Existing site export contains unowned entries: "
                f"{unexpected}"
            )
    if report_path.exists() and not report_path.is_file():
        raise ValueError(f"Report path is not a regular file: {report_path}")
    if output.exists() != report_path.exists():
        raise ValueError(
            "Existing site export is incomplete; output and report ownership "
            "must agree"
        )
    for input_path in inputs:
        sources = {absolute_path(input_path), input_path.resolve(strict=True)}
        if any(
            is_same_or_descendant(source, destination)
            or is_same_or_descendant(destination, source)
            for source in sources
            for destination in (output, report_path)
        ):
            raise ValueError(
                f"Input path must not overlap export destinations: {input_path}"
            )
    return output, report_path


def create_directory_chain(path: Path, label: str, build_root: Path) -> None:
    relative = path.relative_to(build_root)
    current = build_root
    for part in ("", *relative.parts):
        if part:
            current /= part
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink component: {current}")
        if current.exists():
            if not current.is_dir():
                raise ValueError(f"{label} parent is not a directory: {current}")
        else:
            current.mkdir()


def reserve_backup_path(parent: Path, name: str) -> Path:
    reserved = Path(tempfile.mkdtemp(prefix=f".{name}.backup-", dir=parent))
    reserved.rmdir()
    return reserved


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def rollback_target(target: Path, staged: Path, backup: Path | None) -> None:
    """Restore one target based on filesystem state, not interruptible flags."""
    if backup is not None and path_exists(backup):
        if path_exists(target):
            if path_exists(staged):
                raise RuntimeError(
                    f"Cannot preserve both installed and staged paths for {target}"
                )
            os.replace(target, staged)
        os.replace(backup, target)
    elif backup is None and not path_exists(staged) and path_exists(target):
        os.replace(target, staged)


def remove_staged_path(path: Path) -> None:
    if not path_exists(path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def publish_export_atomically(
    output_dir: Path,
    files: dict[str, bytes],
    report: Path,
    report_payload: bytes,
    build_root: Path,
) -> None:
    """Stage the complete export before replacing the prior build artifact."""
    create_directory_chain(output_dir.parent, "output directory", build_root)
    create_directory_chain(report.parent, "report", build_root)
    reject_symlink_components(output_dir, "output directory", build_root)
    reject_symlink_components(report, "report", build_root)

    staged_output = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.stage-", dir=output_dir.parent)
    )
    report_descriptor, staged_report_name = tempfile.mkstemp(
        prefix=f".{report.name}.stage-", dir=report.parent
    )
    staged_report = Path(staged_report_name)
    output_backup = (
        reserve_backup_path(output_dir.parent, output_dir.name)
        if output_dir.exists()
        else None
    )
    report_backup = (
        reserve_backup_path(report.parent, report.name) if report.exists() else None
    )
    rollback_complete = False
    try:
        for name, payload in sorted(files.items()):
            if Path(name).name != name or name in {".", ".."}:
                raise ValueError(f"Unsafe candidate filename: {name!r}")
            (staged_output / name).write_bytes(payload)
        with os.fdopen(report_descriptor, "wb") as stream:
            report_descriptor = -1
            stream.write(report_payload)
            stream.flush()
            os.fsync(stream.fileno())

        if output_backup is not None:
            os.replace(output_dir, output_backup)
        if report_backup is not None:
            os.replace(report, report_backup)
        os.replace(staged_output, output_dir)
        os.replace(staged_report, report)
    except BaseException as error:
        rollback_errors: list[BaseException] = []
        for target, staged, backup in (
            (report, staged_report, report_backup),
            (output_dir, staged_output, output_backup),
        ):
            try:
                rollback_target(target, staged, backup)
            except BaseException as rollback_error:
                rollback_errors.append(rollback_error)
        rollback_complete = not rollback_errors
        if rollback_errors:
            raise RuntimeError(
                "Site export publication failed and rollback was incomplete; "
                "preserved stage/backup paths require inspection"
            ) from error
        raise
    else:
        if output_backup is not None and path_exists(output_backup):
            remove_staged_path(output_backup)
        if report_backup is not None and path_exists(report_backup):
            remove_staged_path(report_backup)
    finally:
        if report_descriptor >= 0:
            os.close(report_descriptor)
        if rollback_complete or (
            path_exists(output_dir) and path_exists(report)
        ):
            remove_staged_path(staged_report)
            remove_staged_path(staged_output)


def command_export(
    args: argparse.Namespace, *, build_root: Path = BUILD_ROOT
) -> None:
    output_dir, report_path = validate_export_destinations(
        args.output_dir,
        args.report,
        [args.publications, args.snapshot, args.policy],
        build_root,
    )
    publications_payload = read_input_bytes(args.publications)
    snapshot_payload = read_input_bytes(args.snapshot)
    policy_payload = read_input_bytes(args.policy)
    records = json.loads(publications_payload)
    snapshot = json.loads(snapshot_payload)
    policy = parse_policy(policy_payload)
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise ValueError("Promoted publications must be a JSON array of objects")
    source = snapshot.get("source") if isinstance(snapshot, dict) else None
    if not isinstance(source, dict):
        raise ValueError("Snapshot has no source provenance")
    validate_snapshot(snapshot)
    snapshot_keys = {
        str(item.get("data", item).get("key", ""))
        for item in snapshot["items"]
        if isinstance(item.get("data", item), dict)
    }
    source_group = int(source["group_id"])
    for record in records:
        zotero_identifiers = identifier_values(record, "dlthings:Identifier")
        matches = [
            match
            for value in zotero_identifiers
            if (match := ZOTERO_IDENTIFIER.fullmatch(value))
        ]
        if not matches:
            raise ValueError(f"{record.get('pid')}: no Zotero source identifier")
        for match in matches:
            if (
                int(match.group(1)) != source_group
                or match.group(2) not in snapshot_keys
            ):
                raise ValueError(
                    f"{record.get('pid')}: Zotero identifier is outside the snapshot"
                )

    counters: Counter[str] = Counter()
    policy_usage = empty_policy_usage()
    rendered = [
        render_publication(record, policy, counters, policy_usage)
        for record in records
    ]
    require_exact_policy_usage(policy, policy_usage)
    by_pid = {record["pid"]: record for record in rendered}
    if len(by_pid) != len(rendered):
        raise ValueError("Site publication PID generation produced a collision")
    files = {
        f"{record['pid'].split('/')[-1]}.yaml": yaml_bytes(record)
        for record in rendered
    }
    if len(files) != len(rendered):
        raise ValueError("Site publication filenames are not unique")

    report = {
        "format_version": 1,
        "source": {
            "api_root": source.get("api_root"),
            "content_sha256": source.get("content_sha256"),
            "fetched_at": source.get("fetched_at"),
            "group_id": source.get("group_id"),
            "library_version": source.get("library_version"),
            "zotero_api_version": source.get("zotero_api_version"),
        },
        "inputs": {
            "policy_sha256": hashlib.sha256(policy_payload).hexdigest(),
            "publications_sha256": hashlib.sha256(
                publications_payload
            ).hexdigest(),
        },
        "output": {
            "publication_count": len(rendered),
            "sha256": directory_digest(files),
            "kinds": dict(
                sorted(Counter(str(record.get("kind")) for record in rendered).items())
            ),
        },
        "reviewed_omissions": dict(sorted(counters.items())),
        "pid_map": [
            {"site_pid": target["pid"], "source_pid": source_record["pid"]}
            for source_record, target in zip(records, rendered, strict=True)
        ],
    }
    report_payload = (
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    publish_export_atomically(
        output_dir,
        files,
        report_path,
        report_payload,
        absolute_path(build_root),
    )
    print(
        f"Rendered {len(rendered)} site publication candidates to "
        f"{output_dir}; review {report_path}."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publications", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main() -> None:
    command_export(build_parser().parse_args())


if __name__ == "__main__":
    main()
