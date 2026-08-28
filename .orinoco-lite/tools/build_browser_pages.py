"""Build the local browser-test site with its repository identity."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
GITHUB_REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}",
)
REMOTE_PREFIXES = (
    "git@github.com:",
    "ssh://git@github.com/",
    "https://github.com/",
    "http://github.com/",
)


def _valid_repository(value: str) -> bool:
    return GITHUB_REPOSITORY.fullmatch(value) is not None and ".." not in value


def _github_repository() -> str:
    """Use the candidate harness coordinate or infer the ordinary origin."""

    value = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if value:
        if not _valid_repository(value):
            raise SystemExit("GITHUB_REPOSITORY must use GitHub OWNER/REPOSITORY form")
        return value

    completed = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise SystemExit(
            "Cannot infer the GitHub repository from origin; set GITHUB_REPOSITORY"
        )
    remote = completed.stdout.strip()
    for prefix in REMOTE_PREFIXES:
        if not remote.startswith(prefix):
            continue
        coordinate = remote.removeprefix(prefix).removesuffix(".git")
        if _valid_repository(coordinate):
            return coordinate
    raise SystemExit(
        "Cannot infer GitHub OWNER/REPOSITORY from origin; set GITHUB_REPOSITORY"
    )


def _base_url(value: str) -> str:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or not value.endswith("/")
        or "?" in value
        or "#" in value
        or ".." in PurePosixPath(value).parts
    ):
        raise SystemExit("Browser-test base URL must be an absolute path ending in /")
    return value


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit("usage: build_browser_pages.py DESTINATION BASE_PATH")
    subprocess.run(
        [
            "orinoco",
            "build",
            "--destination",
            args[0],
            "--base-url",
            _base_url(args[1]),
            "--github-repository",
            _github_repository(),
        ],
        cwd=ROOT,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
