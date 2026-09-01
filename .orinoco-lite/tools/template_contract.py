"""Shared helpers for the content-neutral downstream ownership contract."""

from __future__ import annotations

import fnmatch
import hashlib
import tomllib
from pathlib import Path
from typing import Any, Iterable

import yaml


IGNORED_PARTS = {
    ".git",
    ".pixi",
    ".orinoco",
    "__pycache__",
    "build",
    "generated",
    "node_modules",
    "playwright-report",
    "test-results",
}
SITE_OWNED_CLASSES = {
    "site_specific",
    "extensions",
    "site_acceptance",
    "site_policy",
    "workflow_extensions",
}


class ContractError(RuntimeError):
    """Raised when an ownership or integrity contract is invalid."""


def find_root(start: Path | None = None) -> Path:
    """Find the nearest downstream root from a path inside the checkout."""

    candidate = (start or Path.cwd()).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "orinoco.yaml").is_file():
            return path
    raise ContractError("no orinoco.yaml found in this directory or its parents")


def load_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML mapping with actionable failures."""

    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ContractError(f"cannot read YAML {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractError(f"expected a YAML mapping in {path}")
    return value


def dump_yaml(path: Path, value: dict[str, Any]) -> None:
    """Write stable, reviewable YAML."""

    path.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def path_matches(path: str, pattern: str) -> bool:
    """Match ownership globs with intuitive recursive-directory semantics."""

    normalized = path.strip("/")
    normalized_pattern = pattern.strip("/")
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3].rstrip("/")
        return normalized == prefix or normalized.startswith(prefix + "/")
    return fnmatch.fnmatchcase(normalized, normalized_pattern)


def ownership_classes(contract: dict[str, Any]) -> dict[str, list[str]]:
    """Return validated class-to-pattern mappings."""

    if contract.get("contract_version") != 2:
        raise ContractError("template-ownership.yml must use contract_version 2")
    classes = contract.get("classes")
    if not isinstance(classes, dict):
        raise ContractError("template-ownership.yml classes must be a mapping")
    result: dict[str, list[str]] = {}
    for name, details in classes.items():
        if not isinstance(name, str) or not isinstance(details, dict):
            raise ContractError("each ownership class must be a mapping")
        patterns = details.get("paths")
        if not isinstance(patterns, list) or not all(
            isinstance(item, str) and item for item in patterns
        ):
            raise ContractError(f"ownership class {name!r} has invalid paths")
        result[name] = patterns
    return result


def classify(path: str, classes: dict[str, list[str]]) -> list[str]:
    """Return every ownership class matching a repository-relative path."""

    return [
        name
        for name, patterns in classes.items()
        if any(path_matches(path, pattern) for pattern in patterns)
    ]


def iter_files(root: Path) -> Iterable[Path]:
    """Yield relevant regular files without following repository-local state."""

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if relative.parts[:2] == (".orinoco-lite", "state"):
            continue
        if path.is_symlink():
            yield path
        elif path.is_file():
            yield path


def sha256_file(path: Path) -> str:
    """Hash a regular file or the literal target of a symbolic link."""

    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"symlink\0")
        digest.update(path.readlink().as_posix().encode("utf-8"))
    else:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def snapshot_classes(
    root: Path,
    classes: dict[str, list[str]],
    selected: set[str] = SITE_OWNED_CLASSES,
    excluded: set[str] | None = None,
) -> dict[str, str]:
    """Hash all files in selected ownership classes."""

    excluded = excluded or set()
    snapshot: dict[str, str] = {}
    for path in iter_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        matches = set(classify(relative, classes))
        if matches & selected:
            snapshot[relative] = sha256_file(path)
    return snapshot


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Return sorted added, removed, or byte-changed paths."""

    return sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )


def valid_hex(value: object, length: int) -> bool:
    """Check an exact lower-case hexadecimal integrity value."""

    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def normalize_artifact_url(value: object) -> str | None:
    """Normalize Pixi's direct-PyPI URL form for coordinate comparisons."""

    if not isinstance(value, str) or not value:
        return None
    normalized = value.removeprefix("direct+")
    return normalized.split("#", 1)[0].rstrip("/")


def hashed_engine_url(engine: dict[str, Any]) -> str | None:
    """Return the hash-enforcing direct URL declared in pixi.toml."""

    url = engine.get("url")
    digest = engine.get("sha256")
    if not isinstance(url, str) or not url or "#" in url:
        return None
    if not valid_hex(digest, 64):
        return None
    return f"{url}#sha256={digest}"


def pixi_engine_pin_failures(
    root: Path, engine: dict[str, Any]
) -> list[str]:
    """Validate Pixi 0.73's manifest and lock representation of the engine."""

    failures: list[str] = []
    expected_requirement = hashed_engine_url(engine)
    if expected_requirement is None:
        return ["engine.url and engine.sha256 cannot form a hashed wheel URL"]

    try:
        manifest = tomllib.loads((root / "pixi.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [f"cannot read pixi.toml: {error}"]
    dependency = manifest.get("pypi-dependencies", {}).get("orinoco-lite")
    requirement_url = dependency.get("url") if isinstance(dependency, dict) else None
    if requirement_url != expected_requirement:
        failures.append(
            "pixi.toml orinoco-lite URL must append #sha256=engine.sha256 "
            "to engine.url"
        )

    try:
        pixi_lock = load_yaml(root / "pixi.lock")
    except ContractError as error:
        failures.append(str(error))
        return failures
    packages = pixi_lock.get("packages", [])
    if not isinstance(packages, list):
        failures.append("pixi.lock packages must be a list")
        return failures
    expected_url = normalize_artifact_url(engine.get("url"))
    matches = [
        package
        for package in packages
        if isinstance(package, dict)
        and package.get("name") == "orinoco-lite"
        and package.get("version") == engine.get("version")
        and normalize_artifact_url(package.get("pypi")) == expected_url
    ]
    if len(matches) != 1:
        failures.append(
            "pixi.lock must contain exactly one orinoco-lite direct package "
            "matching engine.version and engine.url"
        )
        return failures
    locked_url = matches[0].get("pypi")
    if locked_url != "direct+" + expected_requirement:
        failures.append(
            "pixi.lock orinoco-lite package must preserve the direct+ URL and "
            "#sha256 digest from pixi.toml"
        )
    locked_digest = matches[0].get("sha256")
    if locked_digest is not None and locked_digest != engine.get("sha256"):
        failures.append("pixi.lock orinoco-lite SHA-256 differs from engine.sha256")
    return failures
