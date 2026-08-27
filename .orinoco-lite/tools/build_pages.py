"""Build the Pages artifact with an explicit, validated base URL."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit


GITHUB_REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}",
)


def _base_url() -> str:
    value = os.environ.get("ORINOCO_BASE_URL", "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit(
            "ORINOCO_BASE_URL must be an absolute HTTP(S) URL; "
            "configure project Pages before running build-pages"
        )
    if parsed.query or parsed.fragment:
        raise SystemExit("ORINOCO_BASE_URL must not contain a query or fragment")
    return value.rstrip("/") + "/"


def _github_repository() -> str:
    """Return the repository identity supplied by the trusted Actions runner."""

    value = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if GITHUB_REPOSITORY.fullmatch(value) is None or ".." in value:
        raise SystemExit(
            "GITHUB_REPOSITORY must use GitHub's OWNER/REPOSITORY form; "
            "the Pages build derives curation identity from its trusted runner"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    destination = Path(args[0]) if args else Path("build/pages")
    subprocess.run(
        [
            "orinoco",
            "build",
            "--destination",
            str(destination),
            "--base-url",
            _base_url(),
            "--github-repository",
            _github_repository(),
        ],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
