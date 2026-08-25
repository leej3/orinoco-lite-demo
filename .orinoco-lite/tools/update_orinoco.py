#!/usr/bin/env python3
"""Apply a conflict-aware, content-preserving Orinoco framework update."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from template_contract import (
    ContractError,
    PROTECTED_UPDATE_CLASSES,
    UPDATE_MUTABLE_PATHS,
    changed_paths,
    classify,
    dump_yaml,
    find_root,
    load_yaml,
    ownership_classes,
    path_matches,
    pixi_engine_pin_failures,
    snapshot_classes,
    valid_hex,
)


LEDGER_PATH = Path(".orinoco-lite/state/framework-update.json")
CLASSIFICATIONS = ("security", "compatibility", "presentation")
WORKFLOW_REFERENCE = re.compile(
    r"^(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"/\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml@(?P<sha>[0-9a-f]{40})$"
)
TEMPLATE_VERSION = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[a-z0-9.-]+)?$"
)
STABLE_TEMPLATE_VERSION = re.compile(
    r"^v(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)$"
)


def timestamp() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    moment = (
        datetime.fromtimestamp(int(epoch), timezone.utc)
        if epoch is not None
        else datetime.now(timezone.utc)
    )
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(
    command: list[str],
    *,
    cwd: Path,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def require_clean_git(root: Path) -> None:
    result = run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=root,
        capture=True,
    )
    if result.returncode != 0:
        raise ContractError(f"cannot inspect Git worktree: {result.stderr.strip()}")
    if result.stdout.strip():
        raise ContractError(
            "framework updates require a clean Git worktree; commit or stash "
            "current changes first"
        )


def coordinates(
    lock: dict[str, Any],
    answers: dict[str, Any],
    *,
    template_version: str,
    template_commit: str,
) -> dict[str, Any]:
    template = lock.get("template", {})
    if not isinstance(template, dict):
        template = {}
    return {
        "template": {
            "source": answers.get("_src_path") or template.get("source"),
            "version": template_version,
            "commit": template_commit,
            "copier_ref": answers.get("_commit"),
        },
        "engine": lock.get("engine"),
        "runtime": lock.get("runtime"),
        "workflow": lock.get("workflow"),
    }


def parse_migrations(values: list[str]) -> list[dict[str, str]]:
    migrations: list[dict[str, str]] = []
    for value in values:
        identifier, separator, summary = value.partition("=")
        if not separator or not identifier.strip() or not summary.strip():
            raise ContractError("--migration must use ID=human-readable-summary")
        migrations.append(
            {"id": identifier.strip(), "summary": summary.strip(), "status": "review"}
        )
    return migrations


def workflow_repository(reference: str | None) -> str | None:
    if reference is None:
        return None
    match = WORKFLOW_REFERENCE.fullmatch(reference)
    if match is None:
        raise ContractError(
            "--workflow-ref must use owner/repository/.github/workflows/"
            "name.yml@ followed by a full 40-hex commit SHA"
        )
    return match.group("repository")


def git_template_source(root: Path, source: object) -> str:
    """Translate supported Copier source forms into a safe Git remote."""

    if not isinstance(source, str) or not source:
        raise ContractError(".copier-answers.yml is missing the template source")
    if source.startswith("gh:"):
        repository = source.removeprefix("gh:")
        if not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
            repository,
        ):
            raise ContractError(f"unsupported GitHub template source: {source}")
        return f"https://github.com/{repository.removesuffix('.git')}.git"
    candidate = Path(source)
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    if candidate.exists():
        return candidate.as_posix()
    if source.startswith(("https://", "ssh://", "git@")):
        return source
    raise ContractError(f"unsupported Copier template source: {source}")


def resolve_template_tag(root: Path, source: object, version: str) -> str:
    """Resolve an exact lightweight or annotated template tag to its commit."""

    if TEMPLATE_VERSION.fullmatch(version) is None:
        raise ContractError("template version must be an exact release tag such as v0.1.0")
    remote = git_template_source(root, source)
    tag = f"refs/tags/{version}"
    result = run(
        [
            "git",
            "-c",
            "protocol.ext.allow=never",
            "ls-remote",
            "--exit-code",
            "--",
            remote,
            tag,
            f"{tag}^{{}}",
        ],
        cwd=root,
        capture=True,
    )
    if result.returncode:
        detail = result.stderr.strip() if result.stderr else "tag was not found"
        raise ContractError(
            f"cannot resolve Copier template tag {version!r}: {detail}"
        )
    references: dict[str, str] = {}
    for line in result.stdout.splitlines():
        commit, separator, reference = line.partition("\t")
        if separator and valid_hex(commit, 40):
            references[reference] = commit
    resolved = references.get(f"{tag}^{{}}") or references.get(tag)
    if resolved is None:
        raise ContractError(f"Copier template tag {version!r} did not resolve to a commit")
    return resolved


def recorded_template(answers: dict[str, Any]) -> tuple[str, str]:
    """Return and validate the exact Copier source and release tag."""

    source = answers.get("_src_path")
    version = answers.get("template_version")
    copier_ref = answers.get("_commit")
    if not isinstance(source, str) or not source:
        raise ContractError(".copier-answers.yml is missing the Copier source")
    if not isinstance(version, str) or TEMPLATE_VERSION.fullmatch(version) is None:
        raise ContractError(
            ".copier-answers.yml template_version must be an exact release tag"
        )
    if copier_ref != version:
        raise ContractError(
            "Copier _commit must equal the declared template_version release tag"
        )
    return source, version


def verify_updated_template(
    answers: dict[str, Any],
    *,
    expected_source: str,
    expected_version: str,
) -> None:
    """Reject an update that did not retain the exact source and target tag."""

    source, version = recorded_template(answers)
    if source != expected_source:
        raise ContractError(
            f"Copier template source changed from {expected_source!r} to {source!r}"
        )
    if version != expected_version:
        raise ContractError(
            f"Copier recorded template {version!r}, not target {expected_version!r}"
        )


def copier_data(args: argparse.Namespace) -> dict[str, Any]:
    """Return the coordinate overlay shared by update and proof renders."""

    return {
        "template_version": args.to_template,
        "engine_version": args.to_engine,
        "engine_url": args.engine_url,
        "engine_sha256": args.engine_sha256,
        "runtime_version": args.to_runtime,
        "runtime_url": args.runtime_url,
        "runtime_sha256": args.runtime_sha256,
        "runtime_manifest_sha256": args.runtime_manifest_sha256,
        "workflow_sha": args.workflow_sha,
        "workflow_ref": args.workflow_ref,
        "workflow_repository": workflow_repository(args.workflow_ref),
    }


def copier_command(args: argparse.Namespace, root: Path, pretend: bool) -> list[str]:
    executable = shutil.which("copier")
    if executable is None:
        raise ContractError("Copier is unavailable; run this command through Pixi")
    command = [
        executable,
        "update",
        "--defaults",
        "--skip-answered",
        "--conflict",
        "rej",
    ]
    if pretend:
        command.append("--pretend")
    if args.to_template:
        command.extend(["--vcs-ref", args.to_template])

    for key, value in copier_data(args).items():
        if value is not None:
            command.extend(["--data", f"{key}={value}"])
    command.append(root.as_posix())
    return command


def conflict_paths(root: Path) -> list[str]:
    result: set[str] = set()
    for path in root.rglob("*.rej"):
        if ".git" not in path.parts:
            result.add(path.relative_to(root).as_posix())
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix == ".rej":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        if any(line.startswith("<<<<<<< ") for line in lines) and any(
            line.startswith(">>>>>>> ") for line in lines
        ):
            result.add(path.relative_to(root).as_posix())
    return sorted(result)


def render_release(
    root: Path,
    source: str,
    version: str,
    data: dict[str, Any],
    destination: Path,
) -> None:
    """Render an exact release without executing template tasks."""

    executable = shutil.which("copier")
    if executable is None:
        raise ContractError("Copier is unavailable; run this command through Pixi")
    data_path = destination.parent / f"{destination.name}-answers.yml"
    dump_yaml(data_path, data)
    result = run(
        [
            executable,
            "copy",
            "--quiet",
            "--defaults",
            "--overwrite",
            "--skip-tasks",
            "--vcs-ref",
            version,
            "--data-file",
            data_path.as_posix(),
            source,
            destination.as_posix(),
        ],
        cwd=root,
        capture=True,
    )
    if result.returncode:
        detail = result.stderr.strip() if result.stderr else "unknown render failure"
        raise ContractError(f"cannot render Copier template {version!r}: {detail}")


def intervening_release_commits(
    root: Path,
    source: str,
    previous_version: str,
    target_version: str,
) -> list[tuple[str, str]]:
    """Return exact commits for stable releases strictly inside the update."""

    previous = STABLE_TEMPLATE_VERSION.fullmatch(previous_version)
    target = STABLE_TEMPLATE_VERSION.fullmatch(target_version)
    if previous is None or target is None:
        return []

    def key(match: re.Match[str]) -> tuple[int, int, int]:
        return tuple(
            int(match.group(name)) for name in ("major", "minor", "patch")
        )

    previous_key = key(previous)
    target_key = key(target)
    if previous_key >= target_key:
        return []
    remote = git_template_source(root, source)
    result = run(
        [
            "git",
            "-c",
            "protocol.ext.allow=never",
            "ls-remote",
            "--tags",
            "--",
            remote,
            "refs/tags/v*",
            "refs/tags/v*^{}",
        ],
        cwd=root,
        capture=True,
    )
    if result.returncode:
        raise ContractError("cannot enumerate intervening Copier template releases")
    references: dict[str, str] = {}
    for line in result.stdout.splitlines():
        commit, separator, reference = line.partition("\t")
        if separator and valid_hex(commit, 40):
            references[reference] = commit
    releases: list[tuple[tuple[int, int, int], str, str]] = []
    prefix = "refs/tags/"
    for reference, commit in references.items():
        if not reference.startswith(prefix) or reference.endswith("^{}"):
            continue
        version = reference.removeprefix(prefix)
        match = STABLE_TEMPLATE_VERSION.fullmatch(version)
        if match is None:
            continue
        version_key = key(match)
        if previous_key < version_key < target_key:
            releases.append(
                (
                    version_key,
                    version,
                    references.get(reference + "^{}", commit),
                )
            )
    return [(version, commit) for _, version, commit in sorted(releases)]


def executable_bits(path: Path) -> int:
    return path.stat().st_mode & 0o111


def equivalent_bootstrap_targets(
    root: Path,
    source: str,
    previous_version: str,
    target_version: str,
    answers: dict[str, Any],
    args: argparse.Namespace,
    current_classes: dict[str, list[str]],
) -> tuple[
    dict[str, tuple[bytes, int]],
    dict[str, list[str]],
    dict[str, list[str]],
]:
    """Preapprove exact target merges and return release ownership maps."""

    base_data = {
        key: value for key, value in answers.items() if not key.startswith("_")
    }
    target_data = dict(base_data)
    target_data.update(
        {key: value for key, value in copier_data(args).items() if value is not None}
    )
    approved: dict[str, tuple[bytes, int]] = {}
    with tempfile.TemporaryDirectory(prefix="orinoco-update-renders-") as temporary:
        workspace = Path(temporary)
        base = workspace / "base"
        target = workspace / "target"
        render_release(root, source, previous_version, base_data, base)
        render_release(root, source, target_version, target_data, target)
        base_classes = ownership_classes(
            load_yaml(base / ".orinoco-lite/template-ownership.yml")
        )
        target_classes = ownership_classes(
            load_yaml(target / ".orinoco-lite/template-ownership.yml")
        )
        release_coordinate_keys = set(copier_data(args)) | {"template_source"}
        release_data = {
            key: value
            for key, value in base_data.items()
            if key not in release_coordinate_keys
        }
        intervening: list[tuple[Path, dict[str, list[str]]]] = []
        for version, commit in intervening_release_commits(
            root, source, previous_version, target_version
        ):
            release = workspace / f"release-{version}"
            render_release(root, source, commit, release_data, release)
            intervening.append(
                (
                    release,
                    ownership_classes(
                        load_yaml(release / ".orinoco-lite/template-ownership.yml")
                    ),
                )
            )
        for target_path in sorted(target.rglob("*")):
            if not target_path.is_file() or target_path.is_symlink():
                continue
            relative = target_path.relative_to(target).as_posix()
            if any(
                classify(relative, mapping) != ["template_owned"]
                for mapping in (base_classes, current_classes, target_classes)
            ):
                continue
            base_path = base / relative
            ours_path = root / relative
            if not ours_path.is_file() or ours_path.is_symlink():
                continue
            ours_bytes = ours_path.read_bytes()
            target_bytes = target_path.read_bytes()
            target_mode = executable_bits(target_path)
            if executable_bits(ours_path) != target_mode:
                continue
            if not base_path.exists():
                if ours_bytes == target_bytes:
                    approved[relative] = (target_bytes, target_mode)
                continue
            if not base_path.is_file() or base_path.is_symlink():
                continue
            base_bytes = base_path.read_bytes()
            if ours_bytes == base_bytes or any(
                b"\0" in value for value in (base_bytes, ours_bytes, target_bytes)
            ):
                continue
            try:
                for value in (base_bytes, ours_bytes, target_bytes):
                    value.decode("utf-8")
            except UnicodeDecodeError:
                continue
            released_equivalent = False
            for release, release_classes in intervening:
                release_path = release / relative
                if (
                    classify(relative, release_classes) == ["template_owned"]
                    and release_path.is_file()
                    and not release_path.is_symlink()
                    and release_path.read_bytes() == ours_bytes
                    and executable_bits(release_path) == target_mode
                ):
                    released_equivalent = True
                    break
            if released_equivalent:
                approved[relative] = (target_bytes, target_mode)
                continue
            merged = subprocess.run(
                [
                    "git",
                    "merge-file",
                    "--stdout",
                    ours_path.as_posix(),
                    base_path.as_posix(),
                    target_path.as_posix(),
                ],
                cwd=root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if merged.returncode == 0 and merged.stdout == target_bytes:
                approved[relative] = (target_bytes, target_mode)
    return approved, base_classes, target_classes


def regenerable_engine_lock_rejections(
    conflicts: list[str],
    *,
    previous_classes: dict[str, list[str]],
    current_classes: dict[str, list[str]],
    target_classes: dict[str, list[str]],
    refresh_pixi_lock: bool,
) -> list[str]:
    """Return every rejection only when all are scheduled engine-lock rebuilds."""

    scheduled = {"orinoco.lock"}
    if refresh_pixi_lock:
        scheduled.add("pixi.lock")
    witnesses: list[str] = []
    for conflict in conflicts:
        if not conflict.endswith(".rej"):
            return []
        relative = conflict.removesuffix(".rej")
        if relative not in scheduled or any(
            classify(relative, classes) != ["engine_lock"]
            for classes in (previous_classes, current_classes, target_classes)
        ):
            return []
        witnesses.append(conflict)
    return sorted(witnesses)


def reconcile_equivalent_rejections(
    root: Path,
    conflicts: list[str],
    approved: dict[str, tuple[bytes, int]],
) -> tuple[list[str], list[str]]:
    """Remove only rejection witnesses preapproved by the O/B/T proof."""

    reconciled: list[str] = []
    for conflict in conflicts:
        if not conflict.endswith(".rej"):
            continue
        relative = conflict.removesuffix(".rej")
        expected = approved.get(relative)
        destination = root / relative
        if (
            expected is None
            or not destination.is_file()
            or destination.is_symlink()
            or destination.read_bytes() != expected[0]
            or executable_bits(destination) != expected[1]
        ):
            continue
        (root / conflict).unlink()
        reconciled.append(relative)
    return conflict_paths(root), sorted(reconciled)


def reconcile_populated_placeholders(
    root: Path,
    content_before: dict[str, str],
    *class_maps: dict[str, list[str]],
) -> list[str]:
    """Remove only newly introduced `.gitkeep` beside protected site data."""

    removed: list[str] = []
    for placeholder in sorted(root.rglob(".gitkeep")):
        relative = placeholder.relative_to(root).as_posix()
        if relative in content_before:
            continue
        classifications = [classify(relative, classes) for classes in class_maps]
        if not classifications or any(
            len(matches) != 1 or matches[0] not in PROTECTED_UPDATE_CLASSES
            for matches in classifications
        ):
            continue
        directory = Path(relative).parent.as_posix()
        prefix = "" if directory == "." else directory + "/"
        has_real_protected_file = any(
            path != relative
            and path.startswith(prefix)
            and Path(path).name != ".gitkeep"
            for path in content_before
        )
        if has_real_protected_file:
            placeholder.unlink()
            removed.append(relative)
    return removed


def update_lock(
    lock: dict[str, Any],
    answers: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if lock.get("lock_version") != 1:
        raise ContractError("orinoco.lock must use lock_version 1")
    engine = lock.setdefault("engine", {})
    runtime = lock.setdefault("runtime", {})
    template = lock.setdefault("template", {})
    workflow = lock.setdefault("workflow", {})
    if not all(isinstance(value, dict) for value in (engine, runtime, template, workflow)):
        raise ContractError("orinoco.lock coordinate sections must be mappings")

    if args.to_engine:
        engine["distribution"] = "orinoco-lite"
        engine["version"] = args.to_engine
    if args.engine_url:
        engine["url"] = args.engine_url
    if args.engine_sha256:
        engine["sha256"] = args.engine_sha256
    if args.to_runtime:
        runtime["version"] = args.to_runtime
    for key, value in (
        ("url", args.runtime_url),
        ("sha256", args.runtime_sha256),
        ("manifest_sha256", args.runtime_manifest_sha256),
    ):
        if value is not None:
            runtime[key] = value
    template["source"] = answers.get("_src_path") or template.get("source")
    template["version"] = (
        args.to_template
        or answers.get("template_version")
        or answers.get("_commit")
        or template.get("version")
    )
    if args.workflow_sha:
        workflow["sha"] = args.workflow_sha
    if args.workflow_ref:
        workflow["repository"] = workflow_repository(args.workflow_ref)
        workflow["ref"] = args.workflow_ref
    return lock


def validate_transition(args: argparse.Namespace) -> None:
    if args.to_template and TEMPLATE_VERSION.fullmatch(args.to_template) is None:
        raise ContractError("--to-template must be an exact release tag such as v0.1.0")
    engine_fields = (args.engine_url, args.engine_sha256)
    if args.to_engine and not all(engine_fields):
        raise ContractError(
            "an engine version update requires --engine-url and --engine-sha256"
        )
    if args.engine_sha256 and not valid_hex(args.engine_sha256, 64):
        raise ContractError("--engine-sha256 must be 64 lower-case hex characters")
    runtime_fields = (
        args.runtime_url,
        args.runtime_sha256,
        args.runtime_manifest_sha256,
    )
    if args.to_runtime and not all(runtime_fields):
        raise ContractError(
            "a runtime version update requires --runtime-url, --runtime-sha256, "
            "and --runtime-manifest-sha256"
        )
    if args.runtime_sha256 and not valid_hex(args.runtime_sha256, 64):
        raise ContractError("--runtime-sha256 must be 64 lower-case hex characters")
    if args.runtime_manifest_sha256 and not valid_hex(
        args.runtime_manifest_sha256, 64
    ):
        raise ContractError(
            "--runtime-manifest-sha256 must be 64 lower-case hex characters"
        )
    if args.workflow_sha and not valid_hex(args.workflow_sha, 40):
        raise ContractError("--workflow-sha must be 40 lower-case hex characters")
    if args.workflow_ref:
        match = WORKFLOW_REFERENCE.fullmatch(args.workflow_ref)
        if match is None:
            workflow_repository(args.workflow_ref)
            raise AssertionError("workflow reference validation did not fail")
        suffix = match.group("sha")
        if args.workflow_sha and suffix != args.workflow_sha:
            raise ContractError("--workflow-ref and --workflow-sha must name the same commit")
    if args.allow_site_change and not args.migration:
        raise ContractError("--allow-site-change requires at least one --migration")


def validate_lock_pins(lock: dict[str, Any]) -> None:
    template = lock.get("template", {})
    engine = lock.get("engine", {})
    runtime = lock.get("runtime", {})
    workflow = lock.get("workflow", {})
    if not all(
        isinstance(value, dict) for value in (template, engine, runtime, workflow)
    ):
        raise ContractError(
            "template, engine, runtime, and workflow lock sections must be mappings"
        )
    if not isinstance(template.get("source"), str) or not template["source"]:
        raise ContractError("template.source must identify the Copier repository")
    version = template.get("version")
    if not isinstance(version, str) or TEMPLATE_VERSION.fullmatch(version) is None:
        raise ContractError("template.version must be an exact release tag")
    engine_digest = engine.get("sha256")
    if not valid_hex(engine_digest, 64) or set(engine_digest) == {"0"}:
        raise ContractError("engine.sha256 is not a published non-zero digest")
    if not isinstance(engine.get("url"), str) or not engine["url"]:
        raise ContractError("engine.url must identify the immutable release wheel")
    for key in ("sha256", "manifest_sha256"):
        value = runtime.get(key)
        if not valid_hex(value, 64) or set(value) == {"0"}:
            raise ContractError(f"runtime.{key} is not a published non-zero digest")
    value = workflow.get("sha")
    if not valid_hex(value, 40) or set(value) == {"0"}:
        raise ContractError("workflow.sha is not a published non-zero commit")
    reference = workflow.get("ref")
    if not isinstance(reference, str) or not reference.endswith("@" + value):
        raise ContractError("workflow.ref must end in the exact workflow.sha pin")


def validate_pixi_engine_pin(root: Path, lock: dict[str, Any]) -> None:
    """Require Pixi's hashed manifest URL and direct lock pin to match."""

    engine = lock.get("engine", {})
    if not isinstance(engine, dict):
        raise ContractError("engine lock section must be a mapping")
    failures = pixi_engine_pin_failures(root, engine)
    if failures:
        raise ContractError("; ".join(failures))


def write_ledger(root: Path, ledger: dict[str, Any]) -> None:
    path = root / LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def disallowed_protected_changes(
    changes: list[str],
    classes: dict[str, list[str]],
    allowed_site_patterns: list[str],
) -> list[str]:
    """Reject every undeclared site-owned change."""

    return [
        path
        for path in changes
        if not any(path_matches(path, pattern) for pattern in allowed_site_patterns)
    ]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--check", action="store_true")
    result.add_argument("--to-template", default=os.environ.get("ORINOCO_UPDATE_TEMPLATE"))
    result.add_argument("--to-engine", default=os.environ.get("ORINOCO_UPDATE_ENGINE"))
    result.add_argument("--engine-url", default=os.environ.get("ORINOCO_ENGINE_URL"))
    result.add_argument("--engine-sha256", default=os.environ.get("ORINOCO_ENGINE_SHA256"))
    result.add_argument("--to-runtime", default=os.environ.get("ORINOCO_UPDATE_RUNTIME"))
    result.add_argument("--runtime-url", default=os.environ.get("ORINOCO_RUNTIME_URL"))
    result.add_argument(
        "--runtime-sha256", default=os.environ.get("ORINOCO_RUNTIME_SHA256")
    )
    result.add_argument(
        "--runtime-manifest-sha256",
        default=os.environ.get("ORINOCO_RUNTIME_MANIFEST_SHA256"),
    )
    result.add_argument("--workflow-sha", default=os.environ.get("ORINOCO_WORKFLOW_SHA"))
    result.add_argument("--workflow-ref", default=os.environ.get("ORINOCO_WORKFLOW_REF"))
    result.add_argument(
        "--classification",
        choices=CLASSIFICATIONS,
        default=os.environ.get("ORINOCO_UPDATE_CLASSIFICATION", "compatibility"),
    )
    result.add_argument("--migration", action="append", default=[])
    result.add_argument("--allow-site-change", action="append", default=[])
    result.add_argument("--allow-dirty", action="store_true")
    result.add_argument("--skip-pixi-lock", action="store_true")
    result.add_argument("--skip-pin-validation", action="store_true")
    result.add_argument("--root", type=Path)
    return result


def execute(args: argparse.Namespace) -> int:
    validate_transition(args)
    root = find_root(args.root)
    if not args.allow_dirty:
        require_clean_git(root)

    answers_path = root / ".copier-answers.yml"
    answers_before = load_yaml(answers_path)
    template_source, previous_template_version = recorded_template(answers_before)
    if args.to_template is None:
        args.to_template = previous_template_version
    if TEMPLATE_VERSION.fullmatch(args.to_template) is None:
        raise ContractError("--to-template must be an exact release tag such as v0.1.0")

    previous_template_commit = resolve_template_tag(
        root, template_source, previous_template_version
    )
    target_template_commit = resolve_template_tag(
        root, template_source, args.to_template
    )

    if args.check:
        result = run(copier_command(args, root, True), cwd=root)
        if result.returncode:
            raise ContractError(f"Copier update check failed with status {result.returncode}")
        resolved_after_check = resolve_template_tag(
            root, template_source, args.to_template
        )
        if resolved_after_check != target_template_commit:
            raise ContractError(
                f"Copier template tag {args.to_template!r} moved from "
                f"{target_template_commit} to {resolved_after_check} during the check"
            )
        print("Copier update check completed without changing the checkout")
        return 0

    ownership = load_yaml(root / ".orinoco-lite/template-ownership.yml")
    classes = ownership_classes(ownership)
    lock_path = root / "orinoco.lock"
    lock_before = load_yaml(lock_path)
    content_before = snapshot_classes(
        root,
        classes,
        selected=PROTECTED_UPDATE_CLASSES,
        excluded=UPDATE_MUTABLE_PATHS,
    )
    migrations = parse_migrations(args.migration)

    existing_conflicts = conflict_paths(root)
    if existing_conflicts:
        raise ContractError(
            "pre-existing conflict artifacts require review before an update: "
            + ", ".join(existing_conflicts)
        )
    (
        approved_equivalent,
        previous_classes,
        target_classes,
    ) = equivalent_bootstrap_targets(
        root,
        template_source,
        previous_template_version,
        args.to_template,
        answers_before,
        args,
        classes,
    )

    copier = copier_command(args, root, False)
    result = run(copier, cwd=root)
    conflicts = conflict_paths(root)
    reconciled_conflicts: list[str] = []
    engine_lock_rejections: list[str] = []
    removed_placeholders: list[str] = []
    if result.returncode == 0:
        conflicts, reconciled_conflicts = reconcile_equivalent_rejections(
            root, conflicts, approved_equivalent
        )
        engine_lock_rejections = regenerable_engine_lock_rejections(
            conflicts,
            previous_classes=previous_classes,
            current_classes=classes,
            target_classes=target_classes,
            refresh_pixi_lock=not args.skip_pixi_lock,
        )
        if engine_lock_rejections:
            conflicts = []
        updated_classes = ownership_classes(
            load_yaml(root / ".orinoco-lite/template-ownership.yml")
        )
        removed_placeholders = reconcile_populated_placeholders(
            root, content_before, classes, updated_classes
        )
    answers_after = load_yaml(answers_path)
    verify_updated_template(
        answers_after,
        expected_source=template_source,
        expected_version=args.to_template,
    )
    resolved_after_update = resolve_template_tag(
        root, template_source, args.to_template
    )
    if resolved_after_update != target_template_commit:
        raise ContractError(
            f"Copier template tag {args.to_template!r} moved from "
            f"{target_template_commit} to {resolved_after_update} during the update"
        )
    lock_after = load_yaml(lock_path)
    content_after = snapshot_classes(
        root,
        classes,
        selected=PROTECTED_UPDATE_CLASSES,
        excluded=UPDATE_MUTABLE_PATHS,
    )
    site_changes = changed_paths(content_before, content_after)

    ledger: dict[str, Any] = {
        "ledger_version": 2,
        "created_at": timestamp(),
        "classification": args.classification,
        "status": "failed" if result.returncode else "updating",
        "previous": coordinates(
            lock_before,
            answers_before,
            template_version=previous_template_version,
            template_commit=previous_template_commit,
        ),
        "target": coordinates(
            lock_after,
            answers_after,
            template_version=args.to_template,
            template_commit=target_template_commit,
        ),
        "site_owned": {
            "checked_files": len(content_before),
            "changed": site_changes,
        },
        "conflicts": conflicts,
        "reconciled_target_equivalent": reconciled_conflicts,
        "removed_populated_placeholders": removed_placeholders,
        "migrations": migrations,
        "validation": {"status": "pending"},
        "rollback": "revert the complete framework update commit",
    }

    if result.returncode:
        write_ledger(root, ledger)
        raise ContractError(f"Copier update failed with status {result.returncode}")
    if conflicts:
        ledger["status"] = "conflicts"
        write_ledger(root, ledger)
        raise ContractError(
            "template-owned conflicts require review: " + ", ".join(conflicts)
        )

    disallowed = disallowed_protected_changes(
        site_changes,
        classes,
        args.allow_site_change,
    )
    if disallowed or (site_changes and not migrations):
        ledger["status"] = "rejected-site-change"
        write_ledger(root, ledger)
        raise ContractError(
            "Copier changed protected site content: "
            + ", ".join(site_changes)
        )

    for conflict in engine_lock_rejections:
        (root / conflict).unlink()

    lock_after = update_lock(lock_after, answers_after, args)
    answers_after["template_version"] = args.to_template
    if args.to_engine:
        answers_after["engine_version"] = args.to_engine
    if args.engine_url:
        answers_after["engine_url"] = args.engine_url
    if args.engine_sha256:
        answers_after["engine_sha256"] = args.engine_sha256
    if args.to_runtime:
        answers_after["runtime_version"] = args.to_runtime
    if args.runtime_url:
        answers_after["runtime_url"] = args.runtime_url
    if args.runtime_sha256:
        answers_after["runtime_sha256"] = args.runtime_sha256
    if args.runtime_manifest_sha256:
        answers_after["runtime_manifest_sha256"] = args.runtime_manifest_sha256
    if args.workflow_sha:
        answers_after["workflow_sha"] = args.workflow_sha
    if args.workflow_ref:
        answers_after["workflow_repository"] = workflow_repository(args.workflow_ref)
        answers_after["workflow_ref"] = args.workflow_ref
    dump_yaml(answers_path, answers_after)
    if not args.skip_pin_validation:
        validate_lock_pins(lock_after)
    dump_yaml(lock_path, lock_after)

    if not args.skip_pixi_lock:
        pixi = shutil.which("pixi")
        if pixi is None:
            raise ContractError("Pixi is unavailable; cannot refresh pixi.lock")
        lock_result = run([pixi, "lock"], cwd=root)
        if lock_result.returncode:
            ledger["status"] = "failed-lock"
            write_ledger(root, ledger)
            raise ContractError(f"pixi lock failed with status {lock_result.returncode}")
        validate_pixi_engine_pin(root, lock_after)

    content_final = snapshot_classes(
        root,
        classes,
        selected=PROTECTED_UPDATE_CLASSES,
        excluded=UPDATE_MUTABLE_PATHS,
    )
    final_changes = changed_paths(content_before, content_final)
    disallowed_final = disallowed_protected_changes(
        final_changes,
        classes,
        args.allow_site_change,
    )
    if disallowed_final or (final_changes and not migrations):
        ledger["site_owned"]["checked_files"] = len(content_final)
        ledger["site_owned"]["changed"] = final_changes
        ledger["status"] = "rejected-site-change"
        write_ledger(root, ledger)
        raise ContractError(
            "the completed update changed site-owned content: "
            + ", ".join(final_changes)
        )

    final_answers = load_yaml(answers_path)
    final_lock = load_yaml(lock_path)
    ledger["target"] = coordinates(
        final_lock,
        final_answers,
        template_version=args.to_template,
        template_commit=target_template_commit,
    )
    ledger["site_owned"]["checked_files"] = len(content_final)
    ledger["site_owned"]["changed"] = final_changes
    ledger["status"] = "human-review" if migrations else "ready-for-review"
    write_ledger(root, ledger)
    print(f"framework update is {ledger['status']}; review {LEDGER_PATH}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return execute(args)
    except ContractError as error:
        print(f"Orinoco update failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
