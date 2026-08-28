#!/usr/bin/env python3
"""Verify downstream ownership, pins, and no-submodule boundaries."""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

from template_contract import (
    ContractError,
    classify,
    find_root,
    iter_files,
    load_yaml,
    normalize_artifact_url,
    ownership_classes,
    pixi_engine_pin_failures,
    valid_hex,
)


REQUIRED_TEMPLATE_FILES = {
    "README.md",
    ".gitignore",
    ".copier-answers.yml",
    "pixi.toml",
    ".orinoco-lite/template-ownership.yml",
    ".github/workflows/validate.yml",
    ".github/workflows/pages.yml",
    ".github/workflows/shacl-vue-proposal.yml",
    ".github/workflows/update-orinoco.yml",
    ".orinoco-lite/README.md",
    ".orinoco-lite/tools/template_contract.py",
    ".orinoco-lite/tools/update_orinoco.py",
    ".orinoco-lite/tools/verify_template_ownership.py",
    ".orinoco-lite/tools/finalize_update_ledger.py",
    ".orinoco-lite/tools/verify_deterministic_build.py",
    ".orinoco-lite/tools/verify_local_preview.py",
    ".orinoco-lite/tools/verify_hugo.py",
    ".orinoco-lite/tools/install_browser_tests.py",
    ".orinoco-lite/tools/shacl_vue_handoff.py",
}
ACTION_REFERENCE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
FULL_SHA_ACTION = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"(?:/\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml)?@[0-9a-f]{40}$"
)


def verify_engine_environment(
    root: Path, lock: dict[str, object], failures: list[str]
) -> None:
    engine = lock.get("engine", {})
    if not isinstance(engine, dict):
        return
    expected_version = engine.get("version")
    installed_distributions = list(metadata.distributions(name="orinoco-lite"))
    if not installed_distributions:
        # Ownership checks may run outside the locked Pixi environment while a
        # repository is being initialized. CI's frozen Pixi install and
        # `orinoco runtime verify` exercise the installed environment.
        installed = None
        direct_url = None
    else:
        installed = installed_distributions[0]
        direct_url = None
        for candidate in installed_distributions:
            try:
                candidate_url = json.loads(
                    candidate.read_text("direct_url.json") or "null"
                )
            except json.JSONDecodeError:
                continue
            if (
                candidate.version == expected_version
                and isinstance(candidate_url, dict)
                and normalize_artifact_url(candidate_url.get("url"))
                == normalize_artifact_url(engine.get("url"))
            ):
                installed = candidate
                direct_url = candidate_url
                break

    installed_version = installed.version if installed is not None else None
    if installed_version is not None and installed_version != expected_version:
        failures.append(
            f"installed orinoco-lite {installed_version} differs from lock "
            f"version {expected_version}"
        )

    if installed is not None:
        direct_url_text = installed.read_text("direct_url.json")
        if direct_url is None and direct_url_text is None:
            failures.append("installed orinoco-lite lacks direct URL provenance")
        elif direct_url is None:
            try:
                direct_url = json.loads(direct_url_text)
            except json.JSONDecodeError:
                failures.append("installed orinoco-lite direct_url.json is invalid")
        if isinstance(direct_url, dict) and normalize_artifact_url(
            direct_url.get("url")
        ) != normalize_artifact_url(engine.get("url")):
            failures.append("installed orinoco-lite direct URL differs from engine.url")

    failures.extend(pixi_engine_pin_failures(root, engine))


def verify(root: Path) -> list[str]:
    failures: list[str] = []
    ownership = load_yaml(root / ".orinoco-lite/template-ownership.yml")
    classes = ownership_classes(ownership)

    missing = sorted(path for path in REQUIRED_TEMPLATE_FILES if not (root / path).is_file())
    failures.extend(f"missing required template file: {path}" for path in missing)

    if (root / ".gitmodules").exists():
        failures.append(".gitmodules is forbidden in a downstream repository")

    for path in iter_files(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            failures.append(f"symbolic link requires explicit review: {relative}")
        matches = classify(relative, classes)
        if not matches:
            failures.append(f"unclassified downstream path: {relative}")
        elif len(matches) > 1:
            failures.append(
                f"ambiguous ownership for {relative}: {', '.join(sorted(matches))}"
            )

    answers = load_yaml(root / ".copier-answers.yml")
    for key in ("_src_path", "template_version", "engine_version"):
        if not isinstance(answers.get(key), str) or not answers[key]:
            failures.append(f".copier-answers.yml is missing {key}")
    if answers.get("_commit") != answers.get("template_version"):
        failures.append("Copier _commit must equal the declared template_version tag")

    lock = load_yaml(root / "orinoco.lock")
    if lock.get("lock_version") != 1:
        failures.append("orinoco.lock must use lock_version 1")
    template = lock.get("template", {})
    if not isinstance(template, dict):
        failures.append("orinoco.lock template must be a mapping")
    else:
        if template.get("source") != answers.get("_src_path"):
            failures.append("template.source differs from Copier _src_path")
        if template.get("version") != answers.get("template_version"):
            failures.append("template.version differs from Copier template_version")
    engine = lock.get("engine", {})
    if not isinstance(engine, dict) or engine.get("distribution") != "orinoco-lite":
        failures.append("orinoco.lock must pin the orinoco-lite distribution")
    elif not valid_hex(engine.get("sha256"), 64):
        failures.append("engine.sha256 must be a 64-character lower-case digest")
    elif not isinstance(engine.get("url"), str) or not engine["url"]:
        failures.append("engine.url must identify an immutable release wheel")
    runtime = lock.get("runtime", {})
    if not isinstance(runtime, dict):
        failures.append("orinoco.lock runtime must be a mapping")
    else:
        if not valid_hex(runtime.get("sha256"), 64):
            failures.append("runtime.sha256 must be a 64-character lower-case digest")
        if not valid_hex(runtime.get("manifest_sha256"), 64):
            failures.append(
                "runtime.manifest_sha256 must be a 64-character lower-case digest"
            )
    workflow = lock.get("workflow", {})
    if not isinstance(workflow, dict) or not valid_hex(workflow.get("sha"), 40):
        failures.append("workflow.sha must be a full 40-character commit SHA")
    elif not isinstance(workflow.get("ref"), str) or not workflow["ref"].endswith(
        "@" + workflow["sha"]
    ):
        failures.append("workflow.ref must end with the exact workflow.sha pin")

    # All-zero coordinates define the content-neutral, unpublished default
    # rendering. Concrete consumers must carry a Pixi lock with matching wheel
    # URL, version, and digest.
    if (
        isinstance(engine, dict)
        and valid_hex(engine.get("sha256"), 64)
        and set(engine["sha256"]) != {"0"}
    ):
        verify_engine_environment(root, lock, failures)

    for workflow_path in sorted((root / ".github" / "workflows").glob("*.yml")):
        text = workflow_path.read_text(encoding="utf-8")
        for reference in ACTION_REFERENCE.findall(text):
            if reference.startswith("./"):
                continue
            if not FULL_SHA_ACTION.fullmatch(reference):
                failures.append(
                    f"workflow action is not pinned by full SHA in "
                    f"{workflow_path.relative_to(root)}: {reference}"
                )

    return failures


def main() -> int:
    try:
        root = find_root()
        failures = verify(root)
    except ContractError as error:
        print(f"ownership verification failed: {error}", file=sys.stderr)
        return 2
    if failures:
        print("ownership verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("ownership contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
