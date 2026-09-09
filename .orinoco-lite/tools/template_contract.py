"""Shared helpers for the content-neutral downstream ownership contract."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml


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


def hashed_package_url(package: dict[str, Any]) -> str | None:
    """Return the hash-enforcing direct URL declared in pixi.toml."""

    url = package.get("url")
    digest = package.get("sha256")
    if not isinstance(url, str) or not url or "#" in url:
        return None
    if not valid_hex(digest, 64):
        return None
    return f"{url}#sha256={digest}"


def pixi_package_pin_failures(
    root: Path, package: dict[str, Any]
) -> list[str]:
    """Validate Pixi 0.73's manifest and lock representation of the package."""

    failures: list[str] = []
    expected_requirement = hashed_package_url(package)
    if expected_requirement is None:
        return ["package.url and package.sha256 cannot form a hashed wheel URL"]

    try:
        manifest = tomllib.loads((root / "pixi.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [f"cannot read pixi.toml: {error}"]
    dependency = manifest.get("pypi-dependencies", {}).get("orinoco-lite")
    requirement_url = dependency.get("url") if isinstance(dependency, dict) else None
    if requirement_url != expected_requirement:
        failures.append(
            "pixi.toml orinoco-lite URL must append #sha256=package.sha256 "
            "to package.url"
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
    expected_url = normalize_artifact_url(package.get("url"))
    matches = [
        locked_package
        for locked_package in packages
        if isinstance(locked_package, dict)
        and locked_package.get("name") == "orinoco-lite"
        and locked_package.get("version") == package.get("version")
        and normalize_artifact_url(locked_package.get("pypi")) == expected_url
    ]
    if len(matches) != 1:
        failures.append(
            "pixi.lock must contain exactly one orinoco-lite direct package "
            "matching package.version and package.url"
        )
        return failures
    locked_url = matches[0].get("pypi")
    if locked_url != "direct+" + expected_requirement:
        failures.append(
            "pixi.lock orinoco-lite package must preserve the direct+ URL and "
            "#sha256 digest from pixi.toml"
        )
    locked_digest = matches[0].get("sha256")
    if locked_digest is not None and locked_digest != package.get("sha256"):
        failures.append("pixi.lock orinoco-lite SHA-256 differs from package.sha256")
    return failures
