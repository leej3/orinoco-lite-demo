#!/usr/bin/env python3
"""Run experimental site-owned metadata source adapters."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tomllib
from types import ModuleType
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "source-adapters" / "metadata" / "sources.toml"
BUILD = ROOT / "build" / "metadata-review"
ADAPTER_API_VERSION = 1
MISSING = object()
sys.modules.setdefault("orinoco_metadata_review", sys.modules[__name__])


class MetadataReviewError(RuntimeError):
    """Report an invalid adapter, result, or evidence replacement."""


@dataclass(frozen=True)
class FileState:
    exists: bool
    payload: bytes | None
    mode: int | None


def pointer_segment(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def value_changes(before: object, after: object, path: str = "") -> list[dict[str, object]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, object]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{pointer_segment(key)}"
            old = before.get(key, MISSING)
            new = after.get(key, MISSING)
            if old is MISSING:
                changes.append({"path": child, "before_present": False, "after": new})
            elif new is MISSING:
                changes.append({"path": child, "before": old, "after_present": False})
            else:
                changes.extend(value_changes(old, new, child))
        return changes
    return [{"path": path or "/", "before": before, "after": after}]


def semantic_diff(
    before: Mapping[str, Mapping[str, object]],
    after: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    before_ids = set(before)
    after_ids = set(after)
    added = [
        {"id": identity, "record": after[identity]}
        for identity in sorted(after_ids - before_ids)
    ]
    removed = [
        {"id": identity, "record": before[identity]}
        for identity in sorted(before_ids - after_ids)
    ]
    changed: list[dict[str, object]] = []
    unchanged = 0
    for identity in sorted(before_ids & after_ids):
        changes = value_changes(before[identity], after[identity])
        if not changes:
            unchanged += 1
            continue
        changed.append(
            {
                "id": identity,
                "changes": changes,
            }
        )
    return {
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": unchanged,
            "different": bool(added or removed or changed),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def load_config(path: Path = CONFIG) -> list[dict[str, object]]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise MetadataReviewError(f"Cannot read adapter manifest {path}: {error}") from error
    if document.get("contract_version") != ADAPTER_API_VERSION:
        raise MetadataReviewError("Adapter manifest contract_version is unsupported")
    sources = document.get("sources")
    if not isinstance(sources, list):
        raise MetadataReviewError("Adapter manifest sources must be a list")
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for source in sources:
        if not isinstance(source, dict):
            raise MetadataReviewError("Every adapter manifest source must be a mapping")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id or source_id in seen:
            raise MetadataReviewError(f"Invalid or duplicate source id {source_id!r}")
        adapter = source.get("adapter")
        if not isinstance(adapter, str) or not adapter:
            raise MetadataReviewError(f"Source {source_id} has no adapter path")
        enabled = source.get("enabled_by_default", True)
        if not isinstance(enabled, bool):
            raise MetadataReviewError(
                f"Source {source_id} enabled_by_default must be a Boolean"
            )
        provenance_identity = source.get("provenance_identity")
        if provenance_identity is not None and (
            not isinstance(provenance_identity, str)
            or not provenance_identity
            or provenance_identity != provenance_identity.strip()
            or provenance_identity.splitlines() != [provenance_identity]
        ):
            raise MetadataReviewError(
                f"Source {source_id} provenance_identity must be one nonempty line"
            )
        seen.add(source_id)
        result.append(source)
    return result


def resolve_provenance_identity(
    source_id: str, path: Path = CONFIG
) -> str:
    """Resolve one reviewed adapter identity from the validated manifest."""

    for source in load_config(path):
        if source["id"] != source_id:
            continue
        identity = source.get("provenance_identity")
        if not isinstance(identity, str):
            raise MetadataReviewError(
                f"Source {source_id} has no reviewed provenance_identity"
            )
        return identity
    raise MetadataReviewError(f"Unknown metadata source {source_id!r}")


def safe_repo_path(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MetadataReviewError(f"{label} must be a non-empty repository-relative path")
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise MetadataReviewError(f"{label} escapes the repository: {value}") from error
    return path


def load_adapter(root: Path, source: Mapping[str, object]) -> ModuleType:
    source_id = str(source["id"])
    path = safe_repo_path(root, source["adapter"], label=f"{source_id} adapter")
    if not path.is_file() or path.is_symlink():
        raise MetadataReviewError(f"Adapter is not an ordinary file: {path}")
    name = f"orinoco_metadata_adapter_{source_id}"
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise MetadataReviewError(f"Cannot load adapter {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    if getattr(module, "ADAPTER_API_VERSION", None) != ADAPTER_API_VERSION:
        raise MetadataReviewError(f"Adapter {source_id} has an unsupported API version")
    if not callable(getattr(module, "review", None)):
        raise MetadataReviewError(f"Adapter {source_id} has no review entry point")
    return module


def validate_result(
    root: Path, source_id: str, result: object, output: Path
) -> dict[str, object]:
    if not isinstance(result, dict):
        raise MetadataReviewError(f"Adapter {source_id} returned a non-mapping result")
    if result.get("adapter_api_version") != ADAPTER_API_VERSION:
        raise MetadataReviewError(f"Adapter {source_id} returned the wrong API version")
    if result.get("source_id") != source_id:
        raise MetadataReviewError(f"Adapter {source_id} returned a mismatched source id")
    if result.get("canonical_promotion") is not False:
        raise MetadataReviewError(f"Adapter {source_id} attempted canonical promotion")
    for field in ("source", "source_diff", "candidate_diff", "canonical_diff"):
        if not isinstance(result.get(field), dict):
            raise MetadataReviewError(f"Adapter {source_id} omitted {field}")
    blockers = result.get("blockers", [])
    if not isinstance(blockers, list) or not all(
        isinstance(blocker, str) and blocker for blocker in blockers
    ):
        raise MetadataReviewError(f"Adapter {source_id} blockers must be strings")
    updates = result.get("evidence_updates")
    if not isinstance(updates, list):
        raise MetadataReviewError(f"Adapter {source_id} evidence_updates must be a list")
    output_resolved = output.resolve()
    for update in updates:
        if not isinstance(update, dict):
            raise MetadataReviewError(f"Adapter {source_id} returned an invalid update")
        if update.get("operation", "replace") == "replace":
            staged = safe_repo_path(root, update.get("staged"), label="staged evidence")
            try:
                staged.relative_to(output_resolved)
            except ValueError as error:
                raise MetadataReviewError("Staged evidence is outside the adapter output") from error
            if not staged.is_file() or staged.is_symlink():
                raise MetadataReviewError(f"Staged evidence is not an ordinary file: {staged}")
    return result


def allowed_destination(root: Path, source_id: str, value: object) -> Path:
    destination = safe_repo_path(root, value, label="evidence destination")
    allowed = ((root / "source-adapters" / source_id / "source").resolve(),)
    if not any(destination == prefix or destination.is_relative_to(prefix) for prefix in allowed):
        raise MetadataReviewError(
            f"Evidence destination is outside the source-adapter root: {destination}"
        )
    if destination.is_symlink():
        raise MetadataReviewError(f"Evidence destination is a symlink: {destination}")
    return destination


def file_state(path: Path) -> FileState:
    if not path.exists():
        return FileState(False, None, None)
    if not path.is_file() or path.is_symlink():
        raise MetadataReviewError(f"Evidence destination is not an ordinary file: {path}")
    return FileState(True, path.read_bytes(), path.stat().st_mode & 0o777)


def replace_file(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(payload)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_updates(root: Path, source_id: str, updates: Sequence[Mapping[str, object]]) -> None:
    plan: list[tuple[str, Path, bytes | None]] = []
    for update in updates:
        operation = str(update.get("operation", "replace"))
        destination = allowed_destination(root, source_id, update.get("destination"))
        if operation == "replace":
            staged = safe_repo_path(root, update.get("staged"), label="staged evidence")
            plan.append((operation, destination, staged.read_bytes()))
        elif operation == "delete":
            plan.append((operation, destination, None))
        else:
            raise MetadataReviewError(f"Unsupported evidence operation {operation!r}")
    states = {destination: file_state(destination) for _, destination, _ in plan}
    try:
        for operation, destination, payload in plan:
            if operation == "replace":
                assert payload is not None
                replace_file(destination, payload)
            elif destination.exists():
                destination.unlink()
    except Exception:
        for destination, state in states.items():
            if state.exists:
                assert state.payload is not None and state.mode is not None
                replace_file(destination, state.payload, state.mode)
            elif destination.exists():
                destination.unlink()
        raise


def render_markdown(report: Mapping[str, object]) -> str:
    lines = ["# Metadata source review", ""]
    for source in report["sources"]:
        assert isinstance(source, dict)
        lines.extend([f"## {source['source_id']}", ""])
        source_meta = source["source"]
        assert isinstance(source_meta, dict)
        lines.append(
            f"Source version: `{source_meta.get('reviewed_version')}` → "
            f"`{source_meta.get('live_version')}`"
        )
        lines.append("")
        collection_difference = source_meta.get("collection_diff")
        if isinstance(collection_difference, dict):
            for change in collection_difference.get("changed", []):
                names = {
                    entry.get("path"): entry
                    for entry in change.get("changes", [])
                    if isinstance(entry, dict)
                }
                name_change = names.get("/name", {})
                lines.append(
                    f"- Collection `{change.get('id')}` changed: "
                    f"`{name_change.get('before')}` → `{name_change.get('after')}`"
                )
        for label, key in (
            ("Source records", "source_diff"),
            ("Transformed candidates", "candidate_diff"),
            ("Canonical metadata impact", "canonical_diff"),
        ):
            difference = source[key]
            assert isinstance(difference, dict)
            summary = difference.get("summary", {})
            lines.append(
                f"- {label}: +{summary.get('added', 0)} "
                f"-{summary.get('removed', 0)} ~{summary.get('changed', 0)} "
                f"={summary.get('unchanged', 0)}"
            )
        lines.extend(
            [
                f"- Canonical promotion performed: `{source['canonical_promotion']}`",
                "",
            ]
        )
        blockers = source.get("blockers", [])
        if blockers:
            lines.extend(["### Blockers", ""])
            lines.extend(f"- {blocker}" for blocker in blockers)
            lines.append("")
    return "\n".join(lines) + "\n"


def run(
    mode: str,
    *,
    root: Path = ROOT,
    config: Path = CONFIG,
    build: Path = BUILD,
    selected_sources: Sequence[str] | None = None,
    source_inputs: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if mode not in {"review", "refresh-evidence"}:
        raise MetadataReviewError(f"Unsupported metadata review mode {mode!r}")
    if build.exists():
        if build.is_symlink() or not build.is_dir():
            raise MetadataReviewError(f"Metadata build root is unsafe: {build}")
        shutil.rmtree(build)
    build.mkdir(parents=True)
    configured_sources = load_config(config)
    configured_ids = {str(source["id"]) for source in configured_sources}
    requested = set(selected_sources or [])
    unknown = requested - configured_ids
    if unknown:
        raise MetadataReviewError(
            "Unknown metadata source(s): " + ", ".join(sorted(unknown))
        )
    inputs = dict(source_inputs or {})
    unknown_inputs = set(inputs) - configured_ids
    if unknown_inputs:
        raise MetadataReviewError(
            "Inputs were supplied for unknown metadata source(s): "
            + ", ".join(sorted(unknown_inputs))
        )
    active_sources = [
        source
        for source in configured_sources
        if (
            str(source["id"]) in requested
            if selected_sources is not None
            else source.get("enabled_by_default", True)
        )
    ]
    if not active_sources:
        raise MetadataReviewError("No metadata sources were selected")

    results: list[dict[str, object]] = []
    all_updates: list[tuple[str, list[dict[str, object]]]] = []
    for source in active_sources:
        source_id = str(source["id"])
        output = build / source_id
        output.mkdir()
        module = load_adapter(root, source)
        context = {
            "mode": mode,
            "root": str(root),
            "output": str(output),
            "config": dict(source),
            "source_input": inputs.get(source_id),
        }
        try:
            adapter_result = module.review(context)
        except Exception as error:
            raise MetadataReviewError(f"Adapter {source_id} failed: {error}") from error
        result = validate_result(root, source_id, adapter_result, output)
        results.append(result)
        all_updates.append((source_id, result["evidence_updates"]))
    report: dict[str, object] = {
        "format": "orinoco-metadata-review",
        "version": 1,
        "mode": mode,
        "canonical_promotion": False,
        "sources": results,
    }
    report_path = build / "report.json"
    report_path.write_bytes(json.dumps(report, indent=2, sort_keys=True).encode() + b"\n")
    (build / "report.md").write_text(render_markdown(report), encoding="utf-8")
    if mode == "refresh-evidence":
        blockers = [
            f"{source['source_id']}: {blocker}"
            for source in results
            for blocker in source.get("blockers", [])
        ]
        if blockers:
            raise MetadataReviewError(
                "Evidence refresh is blocked pending review: " + "; ".join(blockers)
            )
        for source_id, updates in all_updates:
            apply_updates(root, source_id, updates)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "review",
            "refresh-evidence",
            "resolve-provenance-identity",
        ),
    )
    parser.add_argument("--adapter", metavar="SOURCE_ID")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="SOURCE_ID",
        help="run only this source adapter (repeatable)",
    )
    parser.add_argument(
        "--source-input",
        action="append",
        default=[],
        metavar="SOURCE_ID=VALUE",
        help="pass a caller-provided input to one source adapter (repeatable)",
    )
    args = parser.parse_args(argv)
    if args.mode == "resolve-provenance-identity":
        if args.adapter is None:
            parser.error("resolve-provenance-identity requires --adapter")
        if args.only or args.source_input:
            parser.error(
                "resolve-provenance-identity does not accept --only or --source-input"
            )
        try:
            print(resolve_provenance_identity(args.adapter, args.config))
        except MetadataReviewError as error:
            parser.exit(1, f"metadata-review: {error}\n")
        return 0
    if args.adapter is not None:
        parser.error("--adapter is only valid for resolve-provenance-identity")
    source_inputs: dict[str, str] = {}
    for item in args.source_input:
        source_id, separator, value = item.partition("=")
        if not separator or not source_id or not value:
            parser.error("--source-input must be SOURCE_ID=VALUE")
        if source_id in source_inputs:
            parser.error(f"--source-input repeats source {source_id!r}")
        source_inputs[source_id] = value
    try:
        report = run(
            args.mode,
            config=args.config,
            selected_sources=args.only,
            source_inputs=source_inputs,
        )
    except MetadataReviewError as error:
        parser.exit(1, f"metadata-review: {error}\n")
    summaries = {
        source["source_id"]: {
            "source": source["source_diff"]["summary"],
            "candidates": source["candidate_diff"]["summary"],
            "canonical": source["canonical_diff"]["summary"],
            "blockers": source.get("blockers", []),
        }
        for source in report["sources"]
    }
    print(json.dumps(summaries, indent=2, sort_keys=True))
    print(f"Review report: {BUILD / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
