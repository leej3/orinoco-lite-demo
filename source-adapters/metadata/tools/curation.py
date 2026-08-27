#!/usr/bin/env python3
"""Trusted GitHub host for one source-adapter curation pull request.

The module owns transport validation and repository writes, not metadata
mapping policy.  It loads a site adapter from trusted default-branch bytes,
rebuilds its ephemeral :class:`CandidatePlan`, and delegates merge semantics
and the compact decision cache to ``orinoco_lite``.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from html import escape
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from types import ModuleType
from urllib.parse import unquote, urlencode, urlsplit

from linkml_runtime.utils.schemaview import SchemaView
from orinoco_lite.candidates import CandidatePlan
from orinoco_lite.canonical import canonical_json_bytes
from orinoco_lite.config import load_lock, load_workspace
from orinoco_lite.decisions import (
    DecisionCache,
    Disposition,
    load_decision_cache,
    serialize_decision_cache,
    update_decision_cache,
)
from orinoco_lite.finalization import finalize_candidate_plan
from orinoco_lite.projection import validate_semantics
from orinoco_lite.runtime import verify_runtime_directory
from orinoco_lite.validation import validate_workspace


SUBMISSION_FORMAT = "orinoco-lite-curation-submission-v1"
REVIEW_BUNDLE_FORMAT = "orinoco-lite-curation-review-bundle-v1"
SUBMIT_COMMAND = "/curation submit"
ATTRIBUTION = "**AI-generated draft — not reviewed by John**"
ADAPTERS = ("dump-research-info", "zotero")
RECORD_ROOT = PurePosixPath("metadata/records")
ANNOTATION_ROOT = PurePosixPath("metadata/overlays/annotations")
SCHEMA_PATH = PurePosixPath("schema/demo-research-information/unreleased.yaml")
MAX_CANDIDATES = 225
MAX_REVIEW_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_SUBMISSION_COMMENT_CHARACTERS = 65_536
DECISION_CACHES = {
    adapter: PurePosixPath(
        "source-adapters", adapter, "policy", "curation-decisions.yaml"
    )
    for adapter in ADAPTERS
}

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_CLAIM_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_REVIEW_URL = re.compile(
    r"https://github\.com/[^/]+/[^/]+/pull/[1-9][0-9]*"
    r"#issuecomment-[1-9][0-9]*\Z"
)
_REVIEWER = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+\Z")
_COLLAPSED_COMMENT = re.compile(
    r"\A/curation submit\n\n<details>\n\n"
    r"<summary>Complete curation submission JSON</summary>\n\n"
    r"```json\n(?P<payload>.+)\n```\n\n</details>\Z",
    re.DOTALL,
)
_LEGACY_COMMENT = re.compile(
    r"\A/curation submit\n\n```json\n(?P<payload>.+)\n```\Z",
    re.DOTALL,
)
_FRIENDLY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class CurationHostError(RuntimeError):
    """Reject stale, incomplete, untrusted, or inconsistent review state."""


@dataclass(frozen=True)
class SubmissionDecision:
    """One browser-supplied disposition bound to an initial candidate."""

    pid: str
    record_path: str
    operation: str
    disposition: Disposition


@dataclass(frozen=True)
class Submission:
    """The complete authenticated-comment payload."""

    repository: str
    pull_request: int
    proposal_sha: str
    head_sha: str
    adapter: str
    source_coordinate: Mapping[str, object]
    decisions: tuple[SubmissionDecision, ...]


def _line(value: object, label: str, *, backticks: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character in value for character in "\r\n\0")
        or (not backticks and "`" in value)
    ):
        raise CurationHostError(f"{label} must be a non-empty single line")
    return value


def _visible_text(value: object, label: str) -> str:
    """Validate one source-controlled string used as visible Markdown text."""

    return _line(value, label, backticks=True)


def _review_site_base_url(value: object) -> str:
    rendered = _line(value, "Review site base URL", backticks=True)
    parsed = urlsplit(rendered)
    try:
        port = parsed.port
    except ValueError as error:
        raise CurationHostError(
            "Review site base URL has an invalid port"
        ) from error
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or not parsed.path.endswith("/")
        or "\\" in decoded_path
        or any(part in {".", ".."} for part in decoded_path.split("/"))
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname) is None
        or parsed.hostname.startswith(".")
        or parsed.hostname.endswith(".")
        or ".." in parsed.hostname
    ):
        raise CurationHostError(
            "Review site base URL must be an absolute HTTPS directory URL"
        )
    authority = parsed.hostname.lower()
    if port is not None:
        authority += f":{port}"
    return f"https://{authority}{parsed.path}"


def _exact_sha(value: object, label: str) -> str:
    rendered = _line(value, label)
    if _SHA40.fullmatch(rendered) is None:
        raise CurationHostError(f"{label} must be an exact lowercase Git SHA")
    return rendered


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CurationHostError(f"{label} must be a positive integer")
    return value


def _strict_object(
    value: object,
    fields: frozenset[str],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise CurationHostError(f"{label} must be a JSON object")
    actual = frozenset(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unexpected = sorted(actual - fields)
        detail: list[str] = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unexpected:
            detail.append("unexpected " + ", ".join(unexpected))
        raise CurationHostError(f"{label} has invalid fields: {'; '.join(detail)}")
    return value


def _json_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise CurationHostError(f"{label} must be a non-empty JSON object")
    try:
        result = json.loads(canonical_json_bytes(value))
    except (TypeError, ValueError) as error:
        raise CurationHostError(f"{label} is not strict JSON: {error}") from error
    if not isinstance(result, dict) or not result:  # pragma: no cover
        raise AssertionError("canonical JSON object changed its type")
    return result


def _json_without_duplicates(text: str) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CurationHostError(f"JSON repeats field {key!r}")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise CurationHostError(f"JSON constant is not supported: {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=constant,
        )
    except CurationHostError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CurationHostError(f"Submission is not valid JSON: {error}") from error


def _relative_record_path(value: object) -> str:
    rendered = _line(value, "Record path")
    path = PurePosixPath(rendered)
    if (
        path.is_absolute()
        or rendered != path.as_posix()
        or path.suffix.lower() not in {".yaml", ".yml"}
        or path.parts[:2] != ("metadata", "records")
        or len(path.parts) < 3
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
    ):
        raise CurationHostError("Record path is outside metadata/records")
    return rendered


def _safe_metadata_path(value: object) -> str:
    rendered = _line(value, "Metadata path")
    path = PurePosixPath(rendered)
    if (
        path.is_absolute()
        or rendered != path.as_posix()
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
        or (
            path.parts[:2] != RECORD_ROOT.parts
            and path.parts[:3] != ANNOTATION_ROOT.parts
        )
    ):
        raise CurationHostError(f"Unsafe metadata output path: {rendered}")
    return rendered


def _candidate_friendly_ids(plan: CandidatePlan) -> tuple[str, ...]:
    values = tuple(
        PurePosixPath(candidate.record_path).stem for candidate in plan.candidates
    )
    if any(_FRIENDLY_ID.fullmatch(value) is None for value in values):
        raise CurationHostError("Candidate record paths do not yield friendly IDs")
    if len(values) != len(set(values)):
        raise CurationHostError(
            "Candidate record paths do not yield unique friendly IDs"
        )
    return values


def render_review_bundle(
    plan: CandidatePlan,
    *,
    repository: str,
    pull_request: int,
    workflow_run_id: int,
    proposal_sha: str,
) -> dict[str, object]:
    """Build one reproducible, non-authoritative review presentation bundle."""

    repository = _line(repository, "Repository")
    if _REPOSITORY.fullmatch(repository) is None:
        raise CurationHostError("Repository must be OWNER/REPOSITORY")
    pull_request = _positive_integer(pull_request, "Pull request")
    workflow_run_id = _positive_integer(workflow_run_id, "Workflow run ID")
    proposal_sha = _exact_sha(proposal_sha, "Proposal commit")
    if not plan.candidates:
        raise CurationHostError("A curation review bundle requires candidates")
    if len(plan.candidates) > MAX_CANDIDATES:
        raise CurationHostError("Review bundle exceeds the candidate limit")

    candidates: list[dict[str, object]] = []
    for candidate, friendly_id in zip(
        plan.candidates,
        _candidate_friendly_ids(plan),
        strict=True,
    ):
        claim_sha256 = _line(candidate.claim_sha256, "Candidate claim SHA-256")
        if _CLAIM_SHA256.fullmatch(claim_sha256) is None:
            raise CurationHostError("Candidate claim SHA-256 is invalid")
        paths = sorted(change.path for change in candidate.file_changes())
        if not paths:
            raise CurationHostError("Candidate has no metadata paths")
        for path in paths:
            _safe_metadata_path(path)
        candidates.append(
            {
                "pid": _line(candidate.pid, "Candidate PID"),
                "friendly_id": friendly_id,
                "label": _visible_text(candidate.label, "Candidate label"),
                "source_namespace": _line(
                    candidate.source_namespace,
                    "Candidate source namespace",
                    backticks=True,
                ),
                "source_record_id": _line(
                    candidate.source_record_id,
                    "Source record ID",
                    backticks=True,
                ),
                "record_path": _relative_record_path(candidate.record_repository_path),
                "paths": paths,
                "operation": candidate.operation.value,
                "blockers": [
                    _visible_text(blocker, "Candidate blocker")
                    for blocker in candidate.blockers
                ],
                "claim_sha256": claim_sha256,
            }
        )
    return {
        "format": REVIEW_BUNDLE_FORMAT,
        "repository": repository,
        "pull_request": pull_request,
        "workflow_run_id": workflow_run_id,
        "adapter": plan.adapter,
        "metadata_base_sha": _exact_sha(plan.metadata_base, "Metadata base"),
        "proposal_sha": proposal_sha,
        "source_coordinate": _json_object(
            plan.source_coordinate,
            "Review bundle source coordinate",
        ),
        "candidates": candidates,
    }


def review_bundle_bytes(bundle: Mapping[str, object]) -> bytes:
    """Serialize one artifact bundle and enforce the hosted-app input bound."""

    encoded = canonical_json_bytes(bundle) + b"\n"
    if len(encoded) > MAX_REVIEW_BUNDLE_BYTES:
        raise CurationHostError(
            "Review bundle exceeds the 16 MiB uncompressed application limit"
        )
    return encoded


def render_pull_request_body(
    *,
    site_base_url: str,
    repository: str,
    pull_request: int,
    artifact_id: int,
    source_coordinate: Mapping[str, object],
) -> str:
    """Render a concise editable fallback; none of its bytes are authoritative."""

    base_url = _review_site_base_url(site_base_url)
    repository = _line(repository, "Repository")
    if _REPOSITORY.fullmatch(repository) is None:
        raise CurationHostError("Repository must be OWNER/REPOSITORY")
    pull_request = _positive_integer(pull_request, "Pull request")
    artifact_id = _positive_integer(artifact_id, "Artifact ID")
    coordinate = canonical_json_bytes(
        _json_object(source_coordinate, "Source coordinate")
    ).decode("utf-8")
    query = urlencode(
        (
            ("repository", repository),
            ("pull_request", str(pull_request)),
            ("artifact_id", str(artifact_id)),
        )
    )
    review_url = f"{base_url}review/?{query}"
    visible_coordinate = escape(coordinate, quote=True).replace("`", "&#96;")
    return (
        f"{ATTRIBUTION}\n\n"
        "This draft contains public review data. The review bundle is an ephemeral "
        "GitHub Actions artifact subject to the repository's normal retention; the "
        "proposal and review commits remain in Git history.\n\n"
        f"[Open this site's curation review]({review_url})\n\n"
        f"Source coordinate: <code>{visible_coordinate}</code>\n\n"
        "Merge this pull request with a merge commit. Squash and rebase merges are "
        "not conforming.\n"
    )


def parse_submission_comment(body: str) -> Submission:
    """Parse one exact hosted-app comment without trusting reviewer identity."""

    if not isinstance(body, str) or len(body) > MAX_SUBMISSION_COMMENT_CHARACTERS:
        raise CurationHostError("Submission comment is missing or too large")
    normalized = body.rstrip("\r\n")
    match = _COLLAPSED_COMMENT.fullmatch(normalized)
    if match is None:
        match = _LEGACY_COMMENT.fullmatch(normalized)
    if match is None:
        raise CurationHostError("Comment is not an exact /curation submit JSON payload")
    root = _strict_object(
        _json_without_duplicates(match.group("payload")),
        frozenset(
            {
                "format",
                "repository",
                "pull_request",
                "proposal_sha",
                "head_sha",
                "adapter",
                "source_coordinate",
                "decisions",
            }
        ),
        "Submission",
    )
    if root["format"] != SUBMISSION_FORMAT:
        raise CurationHostError(f"Submission format must be {SUBMISSION_FORMAT}")
    repository = _line(root["repository"], "Repository")
    if _REPOSITORY.fullmatch(repository) is None:
        raise CurationHostError("Repository must be OWNER/REPOSITORY")
    pull_request = root["pull_request"]
    if (
        isinstance(pull_request, bool)
        or not isinstance(pull_request, int)
        or pull_request < 1
    ):
        raise CurationHostError("Pull request must be a positive integer")
    adapter = _line(root["adapter"], "Adapter")
    if adapter not in ADAPTERS:
        raise CurationHostError(f"Unsupported adapter: {adapter}")
    raw_decisions = root["decisions"]
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise CurationHostError("Submission decisions must be a non-empty array")
    if len(raw_decisions) > MAX_CANDIDATES:
        raise CurationHostError("Submission exceeds the supported candidate limit")
    decisions: list[SubmissionDecision] = []
    for index, raw in enumerate(raw_decisions):
        item = _strict_object(
            raw,
            frozenset({"pid", "record_path", "operation", "disposition"}),
            f"Decision {index}",
        )
        operation = _line(item["operation"], "Decision operation")
        if operation not in {"add", "modify", "delete"}:
            raise CurationHostError("Decision operation is invalid")
        try:
            disposition = Disposition(item["disposition"])
        except (TypeError, ValueError) as error:
            raise CurationHostError(
                "Decision disposition must be accept, reject, or defer"
            ) from error
        decisions.append(
            SubmissionDecision(
                pid=_line(item["pid"], "Decision PID"),
                record_path=_relative_record_path(item["record_path"]),
                operation=operation,
                disposition=disposition,
            )
        )
    if len({item.pid for item in decisions}) != len(decisions):
        raise CurationHostError("Submission repeats a candidate PID")
    if len({item.record_path for item in decisions}) != len(decisions):
        raise CurationHostError("Submission repeats a candidate record path")
    return Submission(
        repository=repository,
        pull_request=pull_request,
        proposal_sha=_exact_sha(root["proposal_sha"], "Proposal SHA"),
        head_sha=_exact_sha(root["head_sha"], "Head SHA"),
        adapter=adapter,
        source_coordinate=_json_object(
            root["source_coordinate"], "Submission source coordinate"
        ),
        decisions=tuple(decisions),
    )


def submission_mapping(submission: Submission) -> dict[str, object]:
    """Return the normalized machine payload for narrow workflow preflight."""

    return {
        "format": SUBMISSION_FORMAT,
        "repository": submission.repository,
        "pull_request": submission.pull_request,
        "proposal_sha": submission.proposal_sha,
        "head_sha": submission.head_sha,
        "adapter": submission.adapter,
        "source_coordinate": dict(submission.source_coordinate),
        "decisions": [
            {
                "pid": item.pid,
                "record_path": item.record_path,
                "operation": item.operation,
                "disposition": item.disposition.value,
            }
            for item in submission.decisions
        ],
    }


def verify_submission(
    submission: Submission,
    plan: CandidatePlan,
    *,
    repository: str,
    pull_request: int,
    proposal_sha: str,
    head_sha: str,
) -> dict[str, Disposition]:
    """Bind complete browser state to regenerated facts and exact Git state."""

    proposal_sha = _exact_sha(proposal_sha, "Observed proposal SHA")
    head_sha = _exact_sha(head_sha, "Observed head SHA")
    expected_repository = _line(repository, "Observed repository")
    if _REPOSITORY.fullmatch(expected_repository) is None:
        raise CurationHostError("Observed repository must be OWNER/REPOSITORY")
    if (
        isinstance(pull_request, bool)
        or not isinstance(pull_request, int)
        or pull_request < 1
    ):
        raise CurationHostError("Observed pull request must be a positive integer")
    bindings = (
        (submission.repository, expected_repository, "repository"),
        (submission.pull_request, pull_request, "pull request"),
        (submission.proposal_sha, proposal_sha, "proposal SHA"),
        (submission.head_sha, head_sha, "head SHA"),
        (submission.adapter, plan.adapter, "adapter"),
    )
    for actual, expected, label in bindings:
        if actual != expected:
            raise CurationHostError(f"Stale or inconsistent {label}")
    planned_coordinate = canonical_json_bytes(plan.source_coordinate)
    if canonical_json_bytes(submission.source_coordinate) != planned_coordinate:
        raise CurationHostError("Submitted source coordinate is stale")

    expected_decisions = {
        (candidate.pid, candidate.record_repository_path, candidate.operation.value)
        for candidate in plan.candidates
    }
    submitted_decisions = {
        (item.pid, item.record_path, item.operation) for item in submission.decisions
    }
    if submitted_decisions != expected_decisions:
        raise CurationHostError("Submission does not cover the complete candidate set")
    dispositions = {item.pid: item.disposition for item in submission.decisions}
    return dispositions


def _run_git(root: Path, *arguments: str, text: bool = False) -> bytes | str:
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
        raise CurationHostError(f"Git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.decode("utf-8") if text else completed.stdout


def _diff_entries(
    root: Path,
    base_sha: str,
    head_sha: str,
) -> tuple[tuple[str, str], ...]:
    raw = bytes(
        _run_git(
            root,
            "diff",
            "--name-status",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            base_sha,
            head_sha,
            "--",
        )
    ).split(b"\0")
    values = [value for value in raw if value]
    if len(values) % 2:
        raise CurationHostError("Git returned malformed no-rename diff entries")
    entries: list[tuple[str, str]] = []
    for raw_status, raw_path in zip(values[::2], values[1::2], strict=True):
        try:
            status = raw_status.decode("ascii")
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CurationHostError("Git diff path is not UTF-8") from error
        entries.append((status, path))
    return tuple(entries)


def validate_review_history(root: Path, proposal_sha: str, head_sha: str) -> int:
    """Allow only ordinary record/annotation edits after the proposal commit."""

    root = root.resolve()
    proposal_sha = _exact_sha(proposal_sha, "Proposal SHA")
    head_sha = _exact_sha(head_sha, "Head SHA")
    history = str(
        _run_git(
            root,
            "rev-list",
            "--parents",
            f"{proposal_sha}..{head_sha}",
            text=True,
        )
    ).splitlines()
    for entry in history:
        commit, *parents = entry.split()
        if not parents:
            raise CurationHostError("Review branch contains a parentless later commit")
        for parent in parents:
            for status, rendered in _diff_entries(root, parent, commit):
                path = PurePosixPath(rendered)
                if status not in {"A", "M", "D"}:
                    raise CurationHostError(
                        f"Review history uses unsupported {status} change: {path}"
                    )
                if (
                    path.parts[:2] != RECORD_ROOT.parts
                    and path.parts[:3] != ANNOTATION_ROOT.parts
                ):
                    raise CurationHostError(
                        f"Review history changes code or workflow-owned state: {path}"
                    )
    return len(history)


def _head(root: Path) -> str:
    return str(
        _run_git(root, "rev-parse", "--verify", "HEAD^{commit}", text=True)
    ).strip()


def verify_trusted_head(trusted_root: Path, expected_sha: str) -> str:
    """Bind finalization to the exact trusted default checkout bytes."""

    expected_sha = _exact_sha(expected_sha, "Trusted head SHA")
    if _head(trusted_root.resolve()) != expected_sha:
        raise CurationHostError("Trusted checkout HEAD differs from the submitted SHA")
    return expected_sha


def _status_paths(root: Path) -> tuple[str, ...]:
    raw = bytes(
        _run_git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
    )
    values: list[str] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        if len(entry) < 4:
            raise CurationHostError("Git returned malformed worktree status")
        try:
            values.append(entry[3:].decode("utf-8"))
        except UnicodeDecodeError as error:
            raise CurationHostError("Git worktree path is not UTF-8") from error
    return tuple(sorted(values))


def _safe_destination(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or path.as_posix() != relative
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
        or (
            path.parts[:2] != RECORD_ROOT.parts
            and path.parts[:3] != ANNOTATION_ROOT.parts
        )
    ):
        raise CurationHostError(f"Unsafe metadata output path: {relative}")
    destination = root.joinpath(*path.parts)
    for parent in (destination, *destination.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise CurationHostError(f"Metadata output traverses a symlink: {relative}")
    return destination


def stage_plan(root: Path, plan: CandidatePlan) -> tuple[str, ...]:
    """Materialize exact plan bytes for capture by one outer DataLad run."""

    root = root.resolve()
    if _head(root) != plan.metadata_base:
        raise CurationHostError("Proposal worktree HEAD is not the metadata base")
    before = _status_paths(root)
    if before:
        raise CurationHostError("Proposal worktree must be clean")
    changes = plan.file_changes()
    if not changes:
        return ()
    for change in changes:
        destination = _safe_destination(root, change.path)
        actual = destination.read_bytes() if destination.is_file() else None
        if actual != change.baseline:
            raise CurationHostError(
                f"Candidate baseline bytes are stale: {change.path}"
            )
        if change.proposed is None:
            destination.unlink()
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(change.proposed)
    expected = tuple(sorted(change.path for change in changes))
    if _status_paths(root) != expected:
        raise CurationHostError("Proposal materialization changed unexpected paths")
    return expected


def stage_validated_plan(
    root: Path,
    plan: CandidatePlan,
    runtime_root: Path,
) -> tuple[str, ...]:
    """Stage exact proposal bytes and reject an invalid joined workspace."""

    changed = stage_plan(root, plan)
    _validate_joined_workspace(root, runtime_root)
    return changed


def _validate_joined_workspace(root: Path, runtime_root: Path) -> None:
    """Run shared structural and joined semantic validation without projection."""

    workspace = load_workspace(root)
    validate_workspace(workspace)
    validate_semantics(workspace, runtime_root.resolve())


def _commit_parents(root: Path, commit: str) -> tuple[str, ...]:
    line = str(_run_git(root, "show", "-s", "--format=%P", commit, text=True)).strip()
    return tuple(line.split()) if line else ()


def _blob(root: Path, commit: str, path: str) -> bytes | None:
    listing = bytes(
        _run_git(
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
    if not listing:
        return None
    entries = [entry for entry in listing.split(b"\0") if entry]
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise CurationHostError(f"Git returned an ambiguous tree entry: {path}")
    header, raw_path = entries[0].split(b"\t", 1)
    mode, kind, object_id = header.decode("ascii").split(" ")
    if mode != "100644" or kind != "blob" or raw_path.decode("utf-8") != path:
        raise CurationHostError(f"Metadata path is not a regular Git blob: {path}")
    return bytes(_run_git(root, "cat-file", "blob", object_id))


def verify_proposal_commit(
    root: Path,
    plan: CandidatePlan,
    proposal_sha: str,
) -> None:
    """Prove that the one proposal commit is exactly the regenerated plan."""

    proposal_sha = _exact_sha(proposal_sha, "Proposal SHA")
    if _commit_parents(root, proposal_sha) != (plan.metadata_base,):
        raise CurationHostError(
            "Proposal commit is not one commit on the metadata base"
        )
    changes = plan.file_changes()
    expected_paths = tuple(sorted(change.path for change in changes))
    entries = _diff_entries(root, plan.metadata_base, proposal_sha)
    if any(status not in {"A", "M", "D"} for status, _ in entries):
        raise CurationHostError("Proposal commit contains a non-file metadata change")
    actual_paths = tuple(sorted(path for _, path in entries))
    if actual_paths != expected_paths:
        raise CurationHostError("Proposal commit paths do not match the candidate plan")
    operation_by_status = {"A": "add", "M": "modify", "D": "delete"}
    actual_records = {
        path: operation_by_status[status]
        for status, path in entries
        if PurePosixPath(path).parts[:2] == RECORD_ROOT.parts
    }
    expected_records = {
        candidate.record_repository_path: candidate.operation.value
        for candidate in plan.candidates
    }
    if actual_records != expected_records:
        raise CurationHostError(
            "Proposal record operations do not match the complete candidate set"
        )
    for change in changes:
        if _blob(root, plan.metadata_base, change.path) != change.baseline:
            raise CurationHostError(f"Proposal baseline is stale: {change.path}")
        if _blob(root, proposal_sha, change.path) != change.proposed:
            raise CurationHostError(f"Proposal bytes are inconsistent: {change.path}")


def _load_provider(trusted_root: Path, adapter: str) -> ModuleType:
    """Load executable adapter code only from the current trusted checkout."""

    path = trusted_root / "source-adapters" / adapter / "candidates.py"
    if path.is_symlink() or not path.is_file():
        raise CurationHostError(f"Trusted adapter is missing: {path}")
    name = "orinoco_trusted_curation_" + adapter.replace("-", "_")
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise CurationHostError(f"Cannot load trusted adapter: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _schema(root: Path, runtime_root: Path) -> SchemaView:
    lock = load_lock(root / "orinoco.lock")
    report = verify_runtime_directory(
        runtime_root,
        expected_release=lock.runtime.version,
        expected_manifest_sha256=lock.runtime.manifest_sha256,
    )
    schema_path = report.root.joinpath(*SCHEMA_PATH.parts)
    if schema_path.is_symlink() or not schema_path.is_file():
        raise CurationHostError(f"Pinned Things Schema is missing: {schema_path}")
    return SchemaView(str(schema_path))


def build_plan(
    root: Path,
    trusted_root: Path,
    *,
    adapter: str,
    metadata_base: str,
    adapter_agent_pid: str,
    runtime_root: Path,
    scratch: Path,
    source_checkout: Path | None = None,
    source_revision: str | None = None,
    expected_library_version: int | None = None,
) -> CandidatePlan:
    """Build one active-decision-filtered plan with explicit trusted inputs."""

    if adapter not in ADAPTERS:
        raise CurationHostError(f"Unsupported adapter: {adapter}")
    root = root.resolve()
    trusted_root = trusted_root.resolve()
    metadata_base = _exact_sha(metadata_base, "Metadata base")
    if _head(root) != metadata_base:
        raise CurationHostError("Candidate root HEAD is not the metadata base")
    provider = _load_provider(trusted_root, adapter)
    schema = _schema(root, runtime_root.resolve())
    owner = _line(adapter_agent_pid, "Adapter Agent PID", backticks=True)
    if adapter == "dump-research-info":
        if source_checkout is None or source_revision is None:
            raise CurationHostError(
                "dump-research-info requires an exact source checkout and revision"
            )
        if expected_library_version is not None:
            raise CurationHostError(
                "dump-research-info does not use a Zotero library version"
            )
        revision = _exact_sha(source_revision, "Source revision")
        with redirect_stdout(sys.stderr):
            plan = provider.build_candidate_plan(
                root,
                source_checkout.resolve(),
                trusted_root=trusted_root,
                metadata_base=metadata_base,
                expected_source_commit=revision,
                adapter_agent_pid=owner,
                schema=schema,
            )
    else:
        if source_checkout is not None or source_revision is not None:
            raise CurationHostError("Zotero does not use a source checkout")
        if (
            isinstance(expected_library_version, bool)
            or not isinstance(expected_library_version, int)
            or expected_library_version < 1
        ):
            raise CurationHostError("Zotero requires an exact positive library version")
        with redirect_stdout(sys.stderr):
            plan = provider.build_candidate_plan(
                root,
                scratch.resolve(),
                trusted_root=trusted_root,
                metadata_base=metadata_base,
                expected_library_version=expected_library_version,
                adapter_agent_pid=owner,
                schema=schema,
            )
    if not isinstance(plan, CandidatePlan):
        raise CurationHostError("Adapter did not return a CandidatePlan")
    if plan.adapter != adapter or plan.metadata_base != metadata_base:
        raise CurationHostError("Adapter returned inconsistent plan coordinates")
    if plan.adapter_agent_pid != owner:
        raise CurationHostError("Adapter returned a different Agent PID")
    if len(plan.candidates) > MAX_CANDIDATES:
        raise CurationHostError(
            f"Candidate plan exceeds the supported limit of {MAX_CANDIDATES}"
        )
    return plan


def _cache_path(root: Path, adapter: str) -> Path:
    relative = DECISION_CACHES[adapter]
    return root.joinpath(*relative.parts)


def _apply_finalization(
    review_root: Path,
    *,
    plan: CandidatePlan,
    submission: Submission,
    repository: str,
    pull_request: int,
    proposal_sha: str,
    head_sha: str,
    reviewer: str,
    reviewed_at: str,
    review_url: str,
    review_ref: str,
    base_cache: DecisionCache,
    runtime_root: Path,
) -> dict[str, object]:
    dispositions = verify_submission(
        submission,
        plan,
        repository=repository,
        pull_request=pull_request,
        proposal_sha=proposal_sha,
        head_sha=head_sha,
    )
    if _REVIEWER.fullmatch(reviewer) is None:
        raise CurationHostError("Reviewer must be the authenticated GitHub login URL")
    if _REVIEW_URL.fullmatch(review_url) is None:
        raise CurationHostError("Review URL must identify the authenticated comment")
    verify_proposal_commit(review_root, plan, proposal_sha)
    result = finalize_candidate_plan(
        review_root,
        plan=plan,
        proposal_commit=proposal_sha,
        submitted_head=head_sha,
        dispositions=dispositions,
    )
    cache_path = _cache_path(review_root, plan.adapter)
    updated = update_decision_cache(
        base_cache,
        plan,
        dispositions,
        review_ref=review_ref,
        source_coordinate=plan.source_coordinate,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        review_url=review_url,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(serialize_decision_cache(updated))
    _validate_joined_workspace(review_root, runtime_root)
    counts = {
        disposition.value: sum(value is disposition for value in dispositions.values())
        for disposition in Disposition
    }
    return {
        "adapter": plan.adapter,
        "cache_path": DECISION_CACHES[plan.adapter].as_posix(),
        "candidate_count": len(plan.candidates),
        "metadata_changed": result.metadata_changed,
        "changed_paths": list(result.changed_paths),
        "accepted": counts[Disposition.ACCEPT.value],
        "rejected": counts[Disposition.REJECT.value],
        "deferred": counts[Disposition.DEFER.value],
        "source_coordinate": dict(plan.source_coordinate),
    }


def finalize_review(
    review_root: Path,
    *,
    dry_run: bool,
    **arguments: object,
) -> dict[str, object]:
    """Apply finalization, optionally in a disposable exact-head clone."""

    review_root = review_root.resolve()
    if not dry_run:
        return _apply_finalization(review_root, **arguments)  # type: ignore[arg-type]
    with tempfile.TemporaryDirectory(prefix="curation-finalization-dry-run-") as name:
        clone = Path(name) / "review"
        completed = subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                os.fspath(review_root),
                os.fspath(clone),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise CurationHostError(f"Could not create dry-run clone: {detail}")
        head_sha = arguments.get("head_sha")
        if not isinstance(head_sha, str):
            raise CurationHostError("Dry run requires a head SHA")
        _run_git(clone, "checkout", "--detach", head_sha)
        return _apply_finalization(clone, **arguments)  # type: ignore[arg-type]


def _common_plan_arguments(
    parser: argparse.ArgumentParser, *, final: bool = False
) -> None:
    parser.add_argument("--trusted-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--adapter", choices=ADAPTERS, required=True)
    parser.add_argument("--adapter-agent-pid", required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--source-checkout", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--expected-library-version", type=int)
    if final:
        parser.add_argument("--base-root", type=Path, required=True)
    else:
        parser.add_argument("--root", type=Path, required=True)
        parser.add_argument("--metadata-base", required=True)


def _plan_from_args(args: argparse.Namespace, *, final: bool = False) -> CandidatePlan:
    root = args.base_root if final else args.root
    metadata_base = args.metadata_base if not final else args.metadata_base
    if final:
        verify_trusted_head(args.trusted_root, args.trusted_head_sha)
    return build_plan(
        root,
        args.trusted_root,
        adapter=args.adapter,
        metadata_base=metadata_base,
        adapter_agent_pid=args.adapter_agent_pid,
        runtime_root=args.runtime_root,
        scratch=args.scratch,
        source_checkout=args.source_checkout,
        source_revision=args.source_revision,
        expected_library_version=args.expected_library_version,
    )


def _write_json(value: object) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect-submission")
    inspect.add_argument("--comment", type=Path, required=True)

    history = commands.add_parser("validate-review-history")
    history.add_argument("--root", type=Path, required=True)
    history.add_argument("--proposal-sha", required=True)
    history.add_argument("--head-sha", required=True)

    stage = commands.add_parser("stage-proposal")
    _common_plan_arguments(stage)

    inspect_plan = commands.add_parser("inspect-plan")
    _common_plan_arguments(inspect_plan)

    bundle = commands.add_parser("render-review-bundle")
    _common_plan_arguments(bundle)
    bundle.add_argument("--repository", required=True)
    bundle.add_argument("--pull-request", type=int, required=True)
    bundle.add_argument("--workflow-run-id", type=int, required=True)
    bundle.add_argument("--proposal-sha", required=True)
    bundle.add_argument("--output", type=Path, required=True)

    body = commands.add_parser("render-pr-body")
    body.add_argument("--root", type=Path, required=True)
    body.add_argument("--bundle", type=Path, required=True)
    body.add_argument("--artifact-id", type=int, required=True)
    body.add_argument("--output", type=Path, required=True)

    final = commands.add_parser("finalize")
    _common_plan_arguments(final, final=True)
    final.add_argument("--metadata-base", required=True)
    final.add_argument("--trusted-head-sha", required=True)
    final.add_argument("--review-root", type=Path, required=True)
    final.add_argument("--comment", type=Path, required=True)
    final.add_argument("--repository", required=True)
    final.add_argument("--pull-request", type=int, required=True)
    final.add_argument("--proposal-sha", required=True)
    final.add_argument("--head-sha", required=True)
    final.add_argument("--reviewer", required=True)
    final.add_argument("--reviewed-at", required=True)
    final.add_argument("--review-url", required=True)
    final.add_argument("--review-ref", required=True)
    final.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect-submission":
            submission = parse_submission_comment(
                args.comment.read_text(encoding="utf-8")
            )
            _write_json(submission_mapping(submission))
            return 0
        if args.command == "validate-review-history":
            _write_json(
                {
                    "later_commit_count": validate_review_history(
                        args.root,
                        args.proposal_sha,
                        args.head_sha,
                    )
                }
            )
            return 0
        if args.command == "render-pr-body":
            bundle = _strict_object(
                _json_without_duplicates(args.bundle.read_text(encoding="utf-8")),
                frozenset(
                    {
                        "format",
                        "repository",
                        "pull_request",
                        "workflow_run_id",
                        "adapter",
                        "metadata_base_sha",
                        "proposal_sha",
                        "source_coordinate",
                        "candidates",
                    }
                ),
                "Review bundle",
            )
            if bundle["format"] != REVIEW_BUNDLE_FORMAT:
                raise CurationHostError("Review bundle format is unsupported")
            body = render_pull_request_body(
                site_base_url=load_workspace(args.root).base_url,
                repository=bundle["repository"],
                pull_request=bundle["pull_request"],
                artifact_id=args.artifact_id,
                source_coordinate=_json_object(
                    bundle["source_coordinate"],
                    "Review bundle source coordinate",
                ),
            )
            args.output.write_text(body, encoding="utf-8")
            _write_json(
                {
                    "artifact_id": args.artifact_id,
                    "pull_request": bundle["pull_request"],
                    "repository": bundle["repository"],
                }
            )
            return 0
        plan = _plan_from_args(args, final=args.command == "finalize")
        if args.command == "inspect-plan":
            _write_json(
                {
                    "adapter": plan.adapter,
                    "adapter_agent_pid": plan.adapter_agent_pid,
                    "candidate_count": len(plan.candidates),
                    "source_coordinate": dict(plan.source_coordinate),
                }
            )
            return 0
        if args.command == "stage-proposal":
            changed = stage_validated_plan(args.root, plan, args.runtime_root)
            _write_json(
                {
                    "adapter": plan.adapter,
                    "candidate_count": len(plan.candidates),
                    "changed_paths": list(changed),
                    "source_coordinate": dict(plan.source_coordinate),
                }
            )
            return 0
        if args.command == "render-review-bundle":
            verify_proposal_commit(args.root, plan, args.proposal_sha)
            bundle = render_review_bundle(
                plan,
                repository=args.repository,
                pull_request=args.pull_request,
                workflow_run_id=args.workflow_run_id,
                proposal_sha=args.proposal_sha,
            )
            args.output.write_bytes(review_bundle_bytes(bundle))
            _write_json(
                {
                    "adapter": plan.adapter,
                    "candidate_count": len(plan.candidates),
                    "format": REVIEW_BUNDLE_FORMAT,
                    "source_coordinate": dict(plan.source_coordinate),
                }
            )
            return 0
        submission = parse_submission_comment(args.comment.read_text(encoding="utf-8"))
        report = finalize_review(
            args.review_root,
            dry_run=args.dry_run,
            plan=plan,
            submission=submission,
            repository=args.repository,
            pull_request=args.pull_request,
            proposal_sha=args.proposal_sha,
            head_sha=args.head_sha,
            reviewer=args.reviewer,
            reviewed_at=args.reviewed_at,
            review_url=args.review_url,
            review_ref=args.review_ref,
            base_cache=load_decision_cache(
                _cache_path(args.base_root, args.adapter),
                adapter=args.adapter,
            ),
            runtime_root=args.runtime_root,
        )
        _write_json(report)
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"curation host: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
