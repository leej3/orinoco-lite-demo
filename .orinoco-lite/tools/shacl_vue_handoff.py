"""Inspect and materialize the fixed SHACL Vue Git handoff as untrusted data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Sequence


HANDOFF_PATH = ".orinoco-lite/shacl-vue-review-bundle.json"
BUNDLE_FORMAT = "orinoco-shacl-review-bundle"
BUNDLE_VERSION = 2
MAX_BUNDLE_BYTES = 10 * 1024 * 1024
MAX_BUNDLE_RECORDS = 50
RECORD_ROOT = PurePosixPath("site-specific/metadata/records")
ANNOTATION_ROOT = PurePosixPath("site-specific/metadata/overlays/annotations")
SHA40 = re.compile(r"[0-9a-f]{40}")
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class HandoffError(RuntimeError):
    """A Git object is outside the reviewed SHACL Vue handoff boundary."""


def _exact_sha(value: str, label: str) -> str:
    if SHA40.fullmatch(value) is None:
        raise HandoffError(f"{label} must be an exact lowercase Git SHA")
    return value


def _git(root: Path, *arguments: str, text: bool = False) -> bytes | str:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    completed = subprocess.run(
        ["git", "-C", os.fspath(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise HandoffError(f"Git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.decode("utf-8") if text else completed.stdout


def _head(root: Path) -> str:
    return str(_git(root, "rev-parse", "--verify", "HEAD^{commit}", text=True)).strip()


def _parents(root: Path, commit: str) -> tuple[str, ...]:
    line = str(
        _git(root, "rev-list", "--parents", "-n", "1", commit, text=True)
    ).strip()
    values = line.split()
    if not values or values[0] != commit:
        raise HandoffError("Git returned invalid commit parents")
    return tuple(values[1:])


def _diff_entries(
    root: Path,
    parent: str,
    commit: str,
    *,
    cached: bool = False,
) -> tuple[tuple[str, str], ...]:
    arguments = [
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
    ]
    if cached:
        arguments.extend(("--cached", parent))
    else:
        arguments.extend((parent, commit))
    arguments.append("--")
    raw = bytes(_git(root, *arguments)).split(b"\0")
    values = [value for value in raw if value]
    if len(values) % 2:
        raise HandoffError("Git returned malformed no-rename diff entries")
    entries: list[tuple[str, str]] = []
    for raw_status, raw_path in zip(values[::2], values[1::2], strict=True):
        try:
            entries.append((raw_status.decode("ascii"), raw_path.decode("utf-8")))
        except UnicodeDecodeError as error:
            raise HandoffError("Git diff path is not UTF-8") from error
    return tuple(entries)


def _metadata_path(value: str) -> str | None:
    path = PurePosixPath(value)
    if (
        any(character in value for character in "\\\r\n\0")
        or path.is_absolute()
        or path.as_posix() != value
        or path.suffix.lower() not in {".yaml", ".yml"}
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
    ):
        return None
    if path.parts[:3] == RECORD_ROOT.parts and len(path.parts) >= 4:
        return value
    if path.parts[:4] == ANNOTATION_ROOT.parts and len(path.parts) >= 5:
        return value
    return None


def _decision_cache_path(value: str) -> str | None:
    path = PurePosixPath(value)
    if (
        any(character in value for character in "\\\r\n\0")
        or path.is_absolute()
        or path.as_posix() != value
        or len(path.parts) != 3
        or path.parts[:2] != ("site-specific", "curation-records")
        or path.suffix.lower() not in {".yaml", ".yml"}
        or SOURCE_ID.fullmatch(path.stem) is None
    ):
        return None
    return value


def _tree_entry(root: Path, commit: str, path: str) -> tuple[str, str, str] | None:
    listing = bytes(
        _git(
            root,
            "--literal-pathspecs",
            "ls-tree",
            "-z",
            "--full-tree",
            commit,
            "--",
            path,
        )
    )
    entries = [entry for entry in listing.split(b"\0") if entry]
    if not entries:
        return None
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise HandoffError(f"Path is not one Git object: {path}")
    header, raw_path = entries[0].split(b"\t", 1)
    try:
        mode, kind, object_id = header.decode("ascii").split(" ")
        rendered = raw_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise HandoffError(f"Git returned an invalid tree entry: {path}") from error
    if rendered != path:
        raise HandoffError(f"Git returned a mismatched tree path: {path}")
    return mode, kind, object_id


def _tree_blob(root: Path, commit: str, path: str) -> bytes:
    entry = _tree_entry(root, commit, path)
    if entry is None:
        raise HandoffError(f"Path is absent from the Git tree: {path}")
    mode, kind, object_id = entry
    if mode != "100644" or kind != "blob":
        raise HandoffError(f"Path is not a regular Git blob: {path}")
    return bytes(_git(root, "cat-file", "blob", object_id))


def _assert_regular_blob(root: Path, commit: str, path: str) -> None:
    _tree_blob(root, commit, path)


def _status(root: Path) -> bytes:
    return bytes(_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"))


def _merge_base(root: Path, base_sha: str, head_sha: str) -> str:
    values = str(
        _git(root, "merge-base", "--all", base_sha, head_sha, text=True)
    ).splitlines()
    if len(values) != 1 or SHA40.fullmatch(values[0]) is None:
        raise HandoffError("Pull-request history must have one exact merge base")
    return values[0]


def _trusted_merge_parent(
    root: Path,
    parents: Sequence[str],
    base_sha: str,
) -> str | None:
    """Return the sole merge parent already present on the trusted base line."""

    if len(parents) != 2:
        return None
    trusted: list[str] = []
    for parent in parents:
        try:
            if _merge_base(root, parent, base_sha) == parent:
                trusted.append(parent)
        except HandoffError:
            continue
    return trusted[0] if len(trusted) == 1 else None


def _validate_change(
    root: Path,
    parent: str,
    commit: str,
    status: str,
    path: str,
    *,
    allow_handoff: bool,
) -> None:
    if status not in {"A", "M", "D"}:
        raise HandoffError(f"History uses unsupported {status} change: {path}")
    if path == HANDOFF_PATH:
        if not allow_handoff or status != "A":
            raise HandoffError("The fixed handoff may only be added by the exact head")
    elif _metadata_path(path) is None and _decision_cache_path(path) is None:
        raise HandoffError(f"History changes an unapproved path: {path}")
    if status in {"A", "M"}:
        _assert_regular_blob(root, commit, path)
    if status in {"M", "D"}:
        _assert_regular_blob(root, parent, path)


def _validate_history(
    root: Path,
    merge_base: str,
    head_sha: str,
    *,
    base_sha: str,
    handoff: bool,
) -> int:
    lines = str(
        _git(
            root,
            "rev-list",
            "--reverse",
            "--parents",
            f"{merge_base}..{head_sha}",
            text=True,
        )
    ).splitlines()
    if not lines:
        raise HandoffError("Proposal history has no commit")
    for line in lines:
        commit, *parents = line.split()
        if len(parents) == 1:
            parent = parents[0]
        else:
            parent = _trusted_merge_parent(root, parents, base_sha)
            if parent is None or (handoff and commit == head_sha):
                raise HandoffError(
                    "Proposal history merges must have one trusted-base parent"
                )
        entries = _diff_entries(root, parent, commit)
        for status, path in entries:
            _validate_change(
                root,
                parent,
                commit,
                status,
                path,
                allow_handoff=handoff and commit == head_sha,
            )
        if handoff and commit == head_sha and entries != (("A", HANDOFF_PATH),):
            raise HandoffError(
                "The handoff head must add exactly the fixed bundle path"
            )
    return len(lines)


def _read_bundle_blob(root: Path, head_sha: str) -> tuple[dict[str, object], bytes]:
    payload = _tree_blob(root, head_sha, HANDOFF_PATH)
    if len(payload) > MAX_BUNDLE_BYTES:
        raise HandoffError("SHACL Vue bundle exceeds 10 MiB")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HandoffError("SHACL Vue bundle is not valid UTF-8 JSON") from error
    if (
        not isinstance(value, dict)
        or value.get("format") != BUNDLE_FORMAT
        or value.get("version") != BUNDLE_VERSION
        or SHA40.fullmatch(str(value.get("source_commit", ""))) is None
        or not isinstance(value.get("records"), list)
        or not 0 < len(value["records"]) <= MAX_BUNDLE_RECORDS
    ):
        raise HandoffError("SHACL Vue bundle does not satisfy bounded version 2")
    return value, payload


def inspect_proposal(
    root: Path,
    *,
    base_sha: str,
    head_sha: str,
) -> dict[str, object]:
    """Classify one exact head without executing bytes from the proposal."""

    root = root.resolve()
    base_sha = _exact_sha(base_sha, "Base SHA")
    head_sha = _exact_sha(head_sha, "Head SHA")
    if _head(root) != head_sha:
        raise HandoffError("Proposal checkout HEAD differs from the event head")
    if _status(root):
        raise HandoffError("Proposal checkout must be clean")
    parents = _parents(root, head_sha)
    if len(parents) == 1:
        parent_sha = parents[0]
        head_entries = _diff_entries(root, parent_sha, head_sha)
    elif len(parents) == 2 and base_sha in parents:
        parent_sha = base_sha
        head_entries = _diff_entries(root, base_sha, head_sha)
    else:
        fixed = _tree_entry(root, head_sha, HANDOFF_PATH)
        net = _diff_entries(root, base_sha, head_sha)
        if fixed is not None or any(
            _metadata_path(path) is not None
            or _decision_cache_path(path) is not None
            for _status, path in net
        ):
            raise HandoffError(
                "Canonical merge head must include the exact trusted base"
            )
        return {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "parent_sha": "0" * 40,
            "paths": [],
            "phase": "irrelevant",
        }
    changed = {path for _status_name, path in head_entries}
    metadata = sorted(path for path in changed if _metadata_path(path) is not None)
    curation_state = sorted(
        path for path in changed if _decision_cache_path(path) is not None
    )
    if HANDOFF_PATH not in changed and not metadata and not curation_state:
        return {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "parent_sha": parent_sha,
            "paths": [],
            "phase": "irrelevant",
        }
    if HANDOFF_PATH in changed:
        if len(parents) != 1:
            raise HandoffError("The handoff head must be a one-parent commit")
        if head_entries != (("A", HANDOFF_PATH),):
            raise HandoffError(
                "The handoff head must add exactly the fixed bundle path"
            )
        phase = "handoff"
    else:
        outside = sorted(
            path
            for _status_name, path in head_entries
            if path not in metadata and path not in curation_state
        )
        if outside:
            raise HandoffError(
                "Canonical metadata head also changes an unapproved path: "
                + ", ".join(outside)
            )
        phase = "canonical"

    merge_base = _merge_base(root, base_sha, head_sha)
    commit_count = _validate_history(
        root,
        merge_base,
        head_sha,
        base_sha=base_sha,
        handoff=phase == "handoff",
    )
    report: dict[str, object] = {
        "base_sha": base_sha,
        "commit_count": commit_count,
        "head_sha": head_sha,
        "merge_base_sha": merge_base,
        "parent_sha": parent_sha,
        "paths": [HANDOFF_PATH] if phase == "handoff" else metadata,
        "phase": phase,
    }
    if phase == "handoff":
        bundle, _payload = _read_bundle_blob(root, head_sha)
        if bundle["source_commit"] != parent_sha:
            raise HandoffError(
                "Bundle source_commit must equal the exact handoff parent"
            )
        report["source_commit"] = bundle["source_commit"]
        report["record_count"] = len(bundle["records"])
    return report


def extract_bundle(root: Path, *, head_sha: str, output: Path) -> dict[str, object]:
    """Copy the already-validated fixed-path blob to an ephemeral local file."""

    root = root.resolve()
    head_sha = _exact_sha(head_sha, "Head SHA")
    bundle, payload = _read_bundle_blob(root, head_sha)
    output = output.resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise HandoffError("Bundle output must be a regular file")
    output.write_bytes(payload)
    return {
        "bytes": len(payload),
        "output": os.fspath(output),
        "record_count": len(bundle["records"]),
        "source_commit": bundle["source_commit"],
    }


def inspect_materialized_changes(
    root: Path,
    *,
    source_commit: str,
) -> dict[str, object]:
    """Require one nonempty canonical metadata-only worktree change."""

    root = root.resolve()
    source_commit = _exact_sha(source_commit, "Source commit")
    if _head(root) != source_commit:
        raise HandoffError("Materialization checkout is not the exact source commit")
    raw = _status(root).split(b"\0")
    paths: list[str] = []
    for entry in raw:
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b" ":
            raise HandoffError("Git returned malformed worktree status")
        status = entry[:2].decode("ascii", "strict")
        if "R" in status or "C" in status:
            raise HandoffError("Materialized metadata may not rename or copy paths")
        try:
            path = entry[3:].decode("utf-8")
        except UnicodeDecodeError as error:
            raise HandoffError("Materialized path is not UTF-8") from error
        if _metadata_path(path) is None:
            raise HandoffError(f"Materialization changed an unapproved path: {path}")
        filesystem_path = root.joinpath(*PurePosixPath(path).parts)
        if "D" not in status and (
            filesystem_path.is_symlink() or not filesystem_path.is_file()
        ):
            raise HandoffError(f"Materialized path is not a regular file: {path}")
        paths.append(path)
    if not paths:
        raise HandoffError("SHACL Vue bundle produced no canonical metadata change")
    return {"paths": sorted(paths), "source_commit": source_commit}


def verify_materialized_commit(
    root: Path,
    *,
    source_commit: str,
    commit: str,
) -> dict[str, object]:
    """Prove the replacement is one clean metadata commit on the source."""

    root = root.resolve()
    source_commit = _exact_sha(source_commit, "Source commit")
    commit = _exact_sha(commit, "Materialized commit")
    if _head(root) != commit or _status(root):
        raise HandoffError("Materialized commit checkout must be exact and clean")
    if _parents(root, commit) != (source_commit,):
        raise HandoffError("Materialized commit must have the handoff parent")
    entries = _diff_entries(root, source_commit, commit)
    if not entries:
        raise HandoffError("Materialized commit has no metadata change")
    paths: list[str] = []
    for status, path in entries:
        if status not in {"A", "M", "D"} or _metadata_path(path) is None:
            raise HandoffError(
                f"Materialized commit changes an unapproved path: {path}"
            )
        if status in {"A", "M"}:
            _assert_regular_blob(root, commit, path)
        if status in {"M", "D"}:
            _assert_regular_blob(root, source_commit, path)
        paths.append(path)
    if _tree_entry(root, commit, HANDOFF_PATH) is not None:
        raise HandoffError("Materialized commit retained the temporary handoff")
    return {
        "commit": commit,
        "paths": sorted(paths),
        "source_commit": source_commit,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--root", type=Path, required=True)
    inspect.add_argument("--base-sha", required=True)
    inspect.add_argument("--head-sha", required=True)
    extract = commands.add_parser("extract")
    extract.add_argument("--root", type=Path, required=True)
    extract.add_argument("--head-sha", required=True)
    extract.add_argument("--output", type=Path, required=True)
    materialized = commands.add_parser("inspect-materialized")
    materialized.add_argument("--root", type=Path, required=True)
    materialized.add_argument("--source-commit", required=True)
    verify = commands.add_parser("verify-commit")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            value: object = inspect_proposal(
                args.root,
                base_sha=args.base_sha,
                head_sha=args.head_sha,
            )
        elif args.command == "extract":
            value = extract_bundle(
                args.root,
                head_sha=args.head_sha,
                output=args.output,
            )
        elif args.command == "inspect-materialized":
            value = inspect_materialized_changes(
                args.root,
                source_commit=args.source_commit,
            )
        elif args.command == "verify-commit":
            value = verify_materialized_commit(
                args.root,
                source_commit=args.source_commit,
                commit=args.commit,
            )
        else:
            raise AssertionError(f"unhandled command: {args.command}")
    except HandoffError as error:
        print(f"SHACL Vue handoff error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
