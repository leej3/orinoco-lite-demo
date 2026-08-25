from __future__ import annotations

from collections.abc import Mapping
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from urllib.parse import quote

from linkml.validator import Validator
from linkml_runtime.utils.schemaview import SchemaView
import orinoco_lite
from orinoco_lite.annotations import join_annotations
from orinoco_lite.candidates import CandidatePlan
from orinoco_lite.canonical import canonical_yaml_bytes
from orinoco_lite.config import load_workspace
from orinoco_lite.decisions import (
    Disposition,
    load_decision_cache,
    serialize_decision_cache,
)
from orinoco_lite.finalization import finalize_candidate_plan
from orinoco_lite.projection import validate_semantics
from orinoco_lite.validation import validate_workspace
import yaml

from adapter_fixture import neutralize_reviewed_adapter_state


ROOT = Path(__file__).resolve().parents[3]
ZOTERO_AGENT = "https://example.invalid/agents/zotero-interaction-test-v1"
DUMP_AGENT = "https://example.invalid/agents/dump-interaction-test-v1"
ZOTERO_VERSION = 451
REVIEWER = "https://github.com/fixture-curator"
REPOSITORY = "con/test-orinoco-downstream-website"
REVIEWED_ADAPTER_AGENTS = (
    "xyzrins:source-adapters/dump-research-info/v1",
    "xyzrins:source-adapters/zotero/v1",
)


def schema_fixture() -> Path:
    configured = os.environ.get("ORINOCO_RUNTIME_ROOT")
    candidates = []
    if configured:
        candidates.append(
            Path(configured) / "schema/demo-research-information/unreleased.yaml"
        )
    candidates.extend(
        [
            Path(orinoco_lite.__file__).resolve().parents[4]
            / "submodules/things-schemas/src/demo-research-information/unreleased.yaml",
            ROOT.parent / "orinoco-lite-dev/submodules/things-schemas/src/"
            "demo-research-information/unreleased.yaml",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("The pinned Things Schema fixture is unavailable")


SCHEMA_PATH = schema_fixture()
SCHEMA = SchemaView(str(SCHEMA_PATH))


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Cannot load test module {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


ZOTERO = load_module(
    "orinoco_zotero_interaction_candidates",
    ROOT / "source-adapters/zotero/candidates.py",
)
DUMP = load_module(
    "orinoco_dump_interaction_candidates",
    ROOT / "source-adapters/dump-research-info/candidates.py",
)


def git(
    root: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        },
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed:\n{completed.stderr.strip()}"
        )
    return completed


def git_output(root: Path, *arguments: str) -> str:
    return git(root, *arguments).stdout.strip()


def init_repository(root: Path) -> None:
    root.mkdir(parents=True)
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture Curator")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "commit.gpgsign", "false")


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "--no-gpg-sign", "-m", message)
    return git_output(root, "rev-parse", "HEAD")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def metadata_snapshot(root: Path) -> dict[str, bytes]:
    metadata = root / "metadata"
    return {
        path.relative_to(metadata).as_posix(): path.read_bytes()
        for path in sorted(metadata.rglob("*"))
        if path.is_file()
    }


def prepared_repository(destination: Path) -> tuple[Path, str]:
    root = destination / "consumer"
    init_repository(root)
    shutil.copytree(ROOT / "metadata", root / "metadata")
    for adapter in ("zotero", "dump-research-info"):
        shutil.copytree(
            ROOT / "source-adapters" / adapter,
            root / "source-adapters" / adapter,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    neutralize_reviewed_adapter_state(
        root,
        adapter_agent_pids=REVIEWED_ADAPTER_AGENTS,
        decision_caches=tuple(
            Path(f"source-adapters/{adapter}/policy/curation-decisions.yaml")
            for adapter in ("zotero", "dump-research-info")
        ),
    )
    for relative in ("custom", "extensions", "site"):
        shutil.copytree(ROOT / relative, root / relative)
    shutil.copytree(
        ROOT / ".orinoco-lite/provenance",
        root / ".orinoco-lite/provenance",
    )
    for relative in ("orinoco.lock", "orinoco.yaml"):
        shutil.copyfile(ROOT / relative, root / relative)
    shutil.copyfile(ROOT / ".gitignore", root / ".gitignore")
    (root / "build").mkdir()
    (root / "generated").mkdir()
    shutil.copytree(SCHEMA_PATH.parents[1], destination / "runtime/schema")
    agents = (
        (
            "XYZInstrument/zotero-interaction-test-v1.yaml",
            {
                "display_label": "Synthetic Zotero interaction adapter v1",
                "pid": ZOTERO_AGENT,
                "schema_type": "xyzri:XYZInstrument",
            },
        ),
        (
            "XYZProject/dump-interaction-test-v1.yaml",
            {
                "pid": DUMP_AGENT,
                "schema_type": "xyzri:XYZProject",
                "title": "Synthetic Dump interaction adapter v1",
            },
        ),
    )
    for relative, record in agents:
        path = root / "metadata/records" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_yaml_bytes(record))
    return root, commit_all(root, "base")


def write_dump_source(
    source: Path,
    records: dict[str, list[dict[str, object]]],
    message: str,
) -> str:
    for class_name, values in records.items():
        write_json(source / "data/con_site" / f"{class_name}.json", values)
    return commit_all(source, message)


def make_dump_source(
    destination: Path,
    records: dict[str, list[dict[str, object]]],
) -> tuple[Path, str]:
    source = destination / "dump-source"
    init_repository(source)
    return source, write_dump_source(source, records, "source v1")


def build_zotero(
    root: Path,
    metadata_base: str,
    name: str,
    *,
    library_version: int = ZOTERO_VERSION,
) -> CandidatePlan:
    return ZOTERO.build_candidate_plan(
        root,
        root / "build" / name,
        metadata_base=metadata_base,
        expected_library_version=library_version,
        adapter_agent_pid=ZOTERO_AGENT,
        schema=SCHEMA,
    )


def build_dump(
    root: Path,
    source: Path,
    metadata_base: str,
    source_commit: str,
) -> CandidatePlan:
    return DUMP.build_candidate_plan(
        root,
        source,
        metadata_base=metadata_base,
        expected_source_commit=source_commit,
        adapter_agent_pid=DUMP_AGENT,
        schema=SCHEMA,
    )


def apply_plan(root: Path, plan: CandidatePlan, message: str) -> str:
    for change in plan.file_changes():
        destination = root / PurePosixPath(change.path)
        if change.proposed is None:
            destination.unlink()
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(change.proposed)
    return commit_all(root, message)


def cache_path(root: Path, adapter: str) -> Path:
    return root / f"source-adapters/{adapter}/policy/curation-decisions.yaml"


def write_decisions(
    root: Path,
    plan: CandidatePlan,
    disposition: Disposition,
    comment_id: int,
) -> None:
    path = cache_path(root, plan.adapter)
    cache = load_decision_cache(path, adapter=plan.adapter)
    decisions = {candidate.pid: disposition for candidate in plan.candidates}
    updated = cache.updated(
        plan,
        decisions,
        review_ref=f"github-comment:{comment_id}",
        source_coordinate=plan.source_coordinate,
        reviewer=REVIEWER,
        reviewed_at="2026-08-24T12:00:00Z",
        review_url=(
            f"https://github.com/{REPOSITORY}/pull/42#issuecomment-{comment_id}"
        ),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialize_decision_cache(updated))


def accept_plan(
    root: Path,
    plan: CandidatePlan,
    proposal: str,
    comment_id: int,
) -> str:
    result = finalize_candidate_plan(
        root,
        plan=plan,
        proposal_commit=proposal,
        submitted_head=git_output(root, "rev-parse", "HEAD"),
        dispositions={
            candidate.pid: Disposition.ACCEPT for candidate in plan.candidates
        },
    )
    if result.metadata_changed:
        raise AssertionError("An unchanged accepted proposal rewrote metadata")
    write_decisions(root, plan, Disposition.ACCEPT, comment_id)
    return commit_all(root, f"accept {plan.adapter}")


def dump_attribute_record(pid: str, value: str) -> dict[str, object]:
    return {
        "attributes": [
            {
                "predicate": "schema:version",
                "schema_type": "dlthings:AttributeSpecification",
                "value": value,
            }
        ],
        "pid": pid,
    }


def validate_joined_metadata(test: unittest.TestCase, root: Path) -> None:
    validator = Validator(SCHEMA_PATH)
    records_root = root / "metadata/records"
    annotations_root = root / "metadata/overlays/annotations"
    count = 0
    for record_path in sorted(records_root.rglob("*.yaml")):
        if record_path.name == ".dumpthings.yaml":
            continue
        record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
        test.assertIsInstance(record, dict)
        relative = record_path.relative_to(records_root)
        companion_path = annotations_root / relative
        companion = (
            yaml.safe_load(companion_path.read_text(encoding="utf-8"))
            if companion_path.is_file()
            else None
        )
        joined = join_annotations(record, companion)
        schema_type = str(record["schema_type"]).split(":", 1)[-1]
        report = validator.validate(joined, schema_type)
        test.assertEqual([], report.results, relative.as_posix())
        count += 1
    test.assertGreater(count, 0)
    if annotations_root.is_dir():
        for companion_path in annotations_root.rglob("*.yaml"):
            test.assertTrue(
                (records_root / companion_path.relative_to(annotations_root)).is_file()
            )
    workspace = load_workspace(root)
    workspace_report = validate_workspace(workspace)
    semantic_report = validate_semantics(workspace, root.parent / "runtime")
    test.assertEqual(count, workspace_report["records"])
    test.assertEqual(count, semantic_report["records"])


def machine_assertions(
    record: Mapping[str, object],
    companion: Mapping[str, object] | None,
) -> dict[tuple[str, str], list[dict[str, object]]]:
    joined = join_annotations(record, companion)
    result: dict[tuple[str, str], list[dict[str, object]]] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            annotations = value.get("annotations")
            if isinstance(annotations, dict):
                owner = annotations.get("pav:importedBy")
                source = annotations.get("pav:importedFrom")
                if isinstance(owner, dict) and isinstance(source, dict):
                    owner_value = owner.get("annotation_value")
                    source_value = source.get("annotation_value")
                    if isinstance(owner_value, str) and isinstance(source_value, str):
                        result.setdefault((owner_value, source_value), []).append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(joined)
    return result


def revise_zotero_source(root: Path, source_record_id: str) -> int:
    prefix = "item:"
    if not source_record_id.startswith(prefix):
        raise AssertionError("The material-change fixture requires one Zotero item")
    key = source_record_id[len(prefix) :]
    publications_path = (
        root / "source-adapters/zotero/source/candidates/XYZPublication.json"
    )
    publications = json.loads(publications_path.read_text(encoding="utf-8"))
    matches = [
        record
        for record in publications
        if any(
            isinstance(identifier, dict)
            and identifier.get("notation") == f"zotero:group:6197458:item:{key}"
            for identifier in record.get("identifiers", [])
        )
    ]
    if len(matches) != 1:
        raise AssertionError(f"Cannot locate Zotero item {key}")
    publication = matches[0]
    publication["title"] = str(publication["title"]) + " (material revision)"
    publication["display_label"] = publication["title"]
    write_json(publications_path, publications)

    snapshot_path = root / "source-adapters/zotero/source/snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    items = [item for item in snapshot["items"] if item.get("key") == key]
    if len(items) != 1:
        raise AssertionError(f"Cannot locate snapshot item {key}")
    data = items[0].get("data")
    if not isinstance(data, dict):
        raise AssertionError(f"Snapshot item {key} has no data")
    data["title"] = str(data.get("title", "")) + " (material revision)"
    source = snapshot["source"]
    source["library_version"] = int(source["library_version"]) + 1
    payload = json.dumps(
        {"collections": snapshot["collections"], "items": snapshot["items"]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    source["content_sha256"] = hashlib.sha256(payload).hexdigest()
    write_json(snapshot_path, snapshot)
    return int(source["library_version"])


class CrossAdapterInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.destination = Path(self.temporary.name)

    def test_shared_thing_keeps_independent_pav_caches_and_reopens_material_changes(
        self,
    ) -> None:
        root, base = prepared_repository(self.destination)
        zotero = build_zotero(root, base, "shared-zotero")
        shared = next(
            candidate
            for candidate in zotero.candidates
            if candidate.source_record_id.startswith("item:")
        )
        source, source_commit = make_dump_source(
            self.destination,
            {"XYZPublication": [dump_attribute_record(shared.pid, "dump-source-v1")]},
        )

        zotero_proposal = apply_plan(root, zotero, "zotero proposal")
        accept_plan(root, zotero, zotero_proposal, 1001)
        dump = build_dump(
            root,
            source,
            git_output(root, "rev-parse", "HEAD"),
            source_commit,
        )
        self.assertEqual([shared.pid], [candidate.pid for candidate in dump.candidates])
        dump_proposal = apply_plan(root, dump, "dump proposal")
        accept_plan(root, dump, dump_proposal, 1002)

        zotero_cache = load_decision_cache(cache_path(root, "zotero"), adapter="zotero")
        dump_cache = load_decision_cache(
            cache_path(root, "dump-research-info"),
            adapter="dump-research-info",
        )
        self.assertIn(shared.pid, zotero_cache.decisions)
        self.assertIn(shared.pid, dump_cache.decisions)
        self.assertNotEqual(
            zotero_cache.decisions[shared.pid].source_record_id,
            dump_cache.decisions[shared.pid].source_record_id,
        )
        dump_candidate = dump.candidates[0]
        zotero_key = shared.source_record_id.removeprefix("item:")
        zotero_imported_from = (
            f"https://api.zotero.org/groups/6197458/items/{zotero_key}"
        )
        dump_imported_from = (
            "https://github.com/con/dump-research-info/blob/main/"
            "data/con_site/XYZPublication.json#record="
            f"{quote(shared.pid, safe='')}"
        )
        record_path = root / shared.record_repository_path
        companion_path = root / shared.companion_repository_path
        record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
        companion = yaml.safe_load(companion_path.read_text(encoding="utf-8"))
        self.assertIsInstance(record, dict)
        self.assertIsInstance(companion, dict)
        assert isinstance(record, dict)
        assert isinstance(companion, dict)
        provenance = machine_assertions(record, companion)
        self.assertIn((ZOTERO_AGENT, zotero_imported_from), provenance)
        self.assertIn((DUMP_AGENT, dump_imported_from), provenance)
        self.assertTrue(
            any(
                assertion.get("predicate") == "schema:version"
                and assertion.get("value") == "dump-source-v1"
                for assertion in provenance[(DUMP_AGENT, dump_imported_from)]
            )
        )
        validate_joined_metadata(self, root)

        accepted_head = git_output(root, "rev-parse", "HEAD")
        self.assertEqual(
            (),
            build_zotero(root, accepted_head, "zotero-idempotent").candidates,
        )
        self.assertEqual(
            (),
            build_dump(root, source, accepted_head, source_commit).candidates,
        )

        source_commit = write_dump_source(
            source,
            {"XYZPublication": [dump_attribute_record(shared.pid, "dump-source-v2")]},
            "source v2",
        )
        reopened_dump = build_dump(root, source, accepted_head, source_commit)
        self.assertEqual([shared.pid], [item.pid for item in reopened_dump.candidates])
        reopened_dump_candidate = reopened_dump.candidates[0]
        self.assertEqual(
            (
                dump_candidate.source_namespace,
                dump_candidate.source_record_id,
                dump_candidate.pid,
                dump_candidate.record_path,
            ),
            (
                reopened_dump_candidate.source_namespace,
                reopened_dump_candidate.source_record_id,
                reopened_dump_candidate.pid,
                reopened_dump_candidate.record_path,
            ),
        )
        self.assertNotEqual(
            dump_candidate.claim_sha256,
            reopened_dump_candidate.claim_sha256,
        )
        self.assertTrue(
            any(
                assertion.get("predicate") == "schema:version"
                and assertion.get("value") == "dump-source-v2"
                for assertion in reopened_dump_candidate.source_claim["attributes"]
            )
        )
        assert reopened_dump_candidate.proposed_record is not None
        assert reopened_dump_candidate.proposed_companion is not None
        reopened_dump_provenance = machine_assertions(
            reopened_dump_candidate.proposed_record,
            reopened_dump_candidate.proposed_companion,
        )
        self.assertIn(
            (ZOTERO_AGENT, zotero_imported_from),
            reopened_dump_provenance,
        )
        self.assertTrue(
            any(
                assertion.get("predicate") == "schema:version"
                and assertion.get("value") == "dump-source-v2"
                for assertion in reopened_dump_provenance[
                    (DUMP_AGENT, dump_imported_from)
                ]
            )
        )

        library_version = revise_zotero_source(root, shared.source_record_id)
        zotero_source_base = commit_all(root, "zotero source v2")
        reopened_zotero = build_zotero(
            root,
            zotero_source_base,
            "zotero-material",
            library_version=library_version,
        )
        self.assertEqual(
            [shared.pid],
            [item.pid for item in reopened_zotero.candidates],
        )
        reopened_zotero_candidate = reopened_zotero.candidates[0]
        self.assertEqual(
            (
                shared.source_namespace,
                shared.source_record_id,
                shared.pid,
                shared.record_path,
            ),
            (
                reopened_zotero_candidate.source_namespace,
                reopened_zotero_candidate.source_record_id,
                reopened_zotero_candidate.pid,
                reopened_zotero_candidate.record_path,
            ),
        )
        self.assertNotEqual(
            shared.claim_sha256,
            reopened_zotero_candidate.claim_sha256,
        )
        self.assertTrue(
            any(
                assertion.get("predicate") == "dlthings:title"
                and str(assertion.get("value", "")).endswith("(material revision)")
                for assertion in reopened_zotero_candidate.source_claim["attributes"]
            )
        )
        assert reopened_zotero_candidate.proposed_record is not None
        assert reopened_zotero_candidate.proposed_companion is not None
        reopened_zotero_provenance = machine_assertions(
            reopened_zotero_candidate.proposed_record,
            reopened_zotero_candidate.proposed_companion,
        )
        self.assertTrue(
            any(
                assertion.get("predicate") == "dlthings:title"
                and str(assertion.get("value", "")).endswith("(material revision)")
                for assertion in reopened_zotero_provenance[
                    (ZOTERO_AGENT, zotero_imported_from)
                ]
            )
        )
        self.assertTrue(
            any(
                assertion.get("predicate") == "schema:version"
                and assertion.get("value") == "dump-source-v1"
                for assertion in reopened_zotero_provenance[
                    (DUMP_AGENT, dump_imported_from)
                ]
            )
        )

    def test_non_overlapping_proposals_merge_in_both_git_allowed_orders(self) -> None:
        root, base = prepared_repository(self.destination)
        source, source_commit = make_dump_source(
            self.destination,
            {
                "XYZProject": [
                    {
                        "pid": "xyzrins:projects/non-overlap",
                        "title": "Non-overlapping Dump project",
                    }
                ]
            },
        )
        zotero = build_zotero(root, base, "non-overlap-zotero")
        dump = build_dump(root, source, base, source_commit)
        self.assertTrue(zotero.candidates)
        self.assertEqual(1, len(dump.candidates))
        self.assertTrue(
            set(change.path for change in zotero.file_changes()).isdisjoint(
                change.path for change in dump.file_changes()
            )
        )

        git(root, "switch", "--quiet", "--create", "zotero-reviewed", base)
        zotero_commit = apply_plan(root, zotero, "zotero proposal")
        zotero_review = accept_plan(root, zotero, zotero_commit, 3001)
        git(root, "switch", "--quiet", "--create", "dump-reviewed", base)
        dump_commit = apply_plan(root, dump, "dump proposal")
        dump_review = accept_plan(root, dump, dump_commit, 3002)

        heads: list[str] = []
        for branch, first, second in (
            ("zotero-then-dump", "zotero-reviewed", "dump-reviewed"),
            ("dump-then-zotero", "dump-reviewed", "zotero-reviewed"),
        ):
            git(root, "switch", "--quiet", "--create", branch, base)
            git(root, "merge", "--quiet", "--no-ff", "-m", f"merge {first}", first)
            git(root, "merge", "--quiet", "--no-ff", "-m", f"merge {second}", second)
            for retained in (
                zotero_commit,
                zotero_review,
                dump_commit,
                dump_review,
            ):
                self.assertEqual(
                    0,
                    git(
                        root,
                        "merge-base",
                        "--is-ancestor",
                        retained,
                        "HEAD",
                        check=False,
                    ).returncode,
                )
            zotero_cache = load_decision_cache(
                cache_path(root, "zotero"),
                adapter="zotero",
            )
            dump_cache = load_decision_cache(
                cache_path(root, "dump-research-info"),
                adapter="dump-research-info",
            )
            self.assertEqual(
                {candidate.pid for candidate in zotero.candidates},
                set(zotero_cache.decisions),
            )
            self.assertEqual(
                {candidate.pid for candidate in dump.candidates},
                set(dump_cache.decisions),
            )
            self.assertTrue(
                all(
                    decision.disposition is Disposition.ACCEPT
                    for decision in (
                        *zotero_cache.decisions.values(),
                        *dump_cache.decisions.values(),
                    )
                )
            )
            validate_joined_metadata(self, root)
            heads.append(git_output(root, "rev-parse", "HEAD"))

        first_tree = git_output(root, "rev-parse", f"{heads[0]}^{{tree}}")
        second_tree = git_output(root, "rev-parse", f"{heads[1]}^{{tree}}")
        self.assertEqual(first_tree, second_tree)

    def test_conflicting_proposal_is_regenerated_from_new_reviewed_default_base(
        self,
    ) -> None:
        root, base = prepared_repository(self.destination)
        zotero = build_zotero(root, base, "conflict-zotero")
        shared = next(
            candidate
            for candidate in zotero.candidates
            if candidate.source_record_id.startswith("item:")
        )
        source, source_commit = make_dump_source(
            self.destination,
            {
                "XYZPublication": [
                    dump_attribute_record(shared.pid, "conflicting dump claim")
                ]
            },
        )
        obsolete_dump = build_dump(root, source, base, source_commit)
        self.assertEqual(
            [shared.pid], [candidate.pid for candidate in obsolete_dump.candidates]
        )

        git(root, "switch", "--quiet", "--create", "zotero-reviewed", base)
        zotero_commit = apply_plan(root, zotero, "zotero proposal")
        zotero_review = accept_plan(root, zotero, zotero_commit, 4001)
        git(root, "switch", "--quiet", "--create", "obsolete-dump", base)
        obsolete_commit = apply_plan(root, obsolete_dump, "obsolete dump proposal")

        git(root, "switch", "--quiet", "main")
        git(
            root,
            "merge",
            "--quiet",
            "--no-ff",
            "-m",
            "merge reviewed zotero proposal",
            "zotero-reviewed",
        )
        new_base = git_output(root, "rev-parse", "HEAD")
        for retained in (zotero_commit, zotero_review):
            self.assertEqual(
                0,
                git(
                    root,
                    "merge-base",
                    "--is-ancestor",
                    retained,
                    new_base,
                    check=False,
                ).returncode,
            )
        reviewed_zotero_cache = load_decision_cache(
            cache_path(root, "zotero"),
            adapter="zotero",
        )
        self.assertEqual(
            {candidate.pid for candidate in zotero.candidates},
            set(reviewed_zotero_cache.decisions),
        )
        validate_joined_metadata(self, root)
        conflict = git(
            root,
            "merge",
            "--no-ff",
            "-m",
            "merge obsolete dump proposal",
            "obsolete-dump",
            check=False,
        )
        self.assertNotEqual(0, conflict.returncode)
        self.assertIn(
            shared.companion_repository_path,
            git_output(root, "diff", "--name-only", "--diff-filter=U").splitlines(),
        )
        git(root, "merge", "--abort")
        self.assertEqual(new_base, git_output(root, "rev-parse", "HEAD"))

        git(root, "switch", "--quiet", "--create", "regenerated-dump", new_base)
        regenerated = build_dump(root, source, new_base, source_commit)
        self.assertEqual([shared.pid], [item.pid for item in regenerated.candidates])
        obsolete_candidate = obsolete_dump.candidates[0]
        regenerated_candidate = regenerated.candidates[0]
        self.assertEqual(
            (
                obsolete_candidate.source_namespace,
                obsolete_candidate.source_record_id,
                obsolete_candidate.pid,
                obsolete_candidate.record_path,
                obsolete_candidate.operation,
            ),
            (
                regenerated_candidate.source_namespace,
                regenerated_candidate.source_record_id,
                regenerated_candidate.pid,
                regenerated_candidate.record_path,
                regenerated_candidate.operation,
            ),
        )
        self.assertEqual(
            dict(obsolete_dump.source_coordinate),
            dict(regenerated.source_coordinate),
        )
        self.assertEqual(
            dict(obsolete_candidate.source_claim),
            dict(regenerated_candidate.source_claim),
        )
        self.assertEqual(
            obsolete_candidate.claim_sha256,
            regenerated_candidate.claim_sha256,
        )
        regenerated_commit = apply_plan(root, regenerated, "regenerated dump proposal")
        self.assertEqual(
            new_base,
            git_output(root, "show", "--no-patch", "--format=%P", regenerated_commit),
        )
        regenerated_review = accept_plan(
            root,
            regenerated,
            regenerated_commit,
            4002,
        )
        git(root, "switch", "--quiet", "main")
        git(
            root,
            "merge",
            "--quiet",
            "--no-ff",
            "-m",
            "merge reviewed regenerated dump proposal",
            "regenerated-dump",
        )
        for retained in (
            zotero_commit,
            zotero_review,
            regenerated_commit,
            regenerated_review,
        ):
            self.assertEqual(
                0,
                git(
                    root,
                    "merge-base",
                    "--is-ancestor",
                    retained,
                    "HEAD",
                    check=False,
                ).returncode,
            )
        self.assertNotEqual(
            0,
            git(
                root,
                "merge-base",
                "--is-ancestor",
                obsolete_commit,
                "HEAD",
                check=False,
            ).returncode,
        )
        final_zotero_cache = load_decision_cache(
            cache_path(root, "zotero"),
            adapter="zotero",
        )
        final_dump_cache = load_decision_cache(
            cache_path(root, "dump-research-info"),
            adapter="dump-research-info",
        )
        self.assertIn(shared.pid, final_zotero_cache.decisions)
        self.assertIn(shared.pid, final_dump_cache.decisions)
        validate_joined_metadata(self, root)

    def test_all_rejected_keeps_review_lineage_with_base_equivalent_metadata(
        self,
    ) -> None:
        root, base = prepared_repository(self.destination)
        before = metadata_snapshot(root)
        source, source_commit = make_dump_source(
            self.destination,
            {
                "XYZProject": [
                    {
                        "pid": "xyzrins:projects/all-rejected",
                        "title": "Rejected source proposal",
                    }
                ]
            },
        )
        plan = build_dump(root, source, base, source_commit)
        self.assertEqual(1, len(plan.candidates))

        git(root, "switch", "--quiet", "--create", "all-rejected", base)
        proposal = apply_plan(root, plan, "dump proposal")
        result = finalize_candidate_plan(
            root,
            plan=plan,
            proposal_commit=proposal,
            submitted_head=proposal,
            dispositions={plan.candidates[0].pid: Disposition.REJECT},
        )
        self.assertTrue(result.metadata_changed)
        write_decisions(root, plan, Disposition.REJECT, 2001)
        review = commit_all(root, "reject dump proposal")
        self.assertEqual(before, metadata_snapshot(root))
        self.assertEqual(
            proposal,
            git_output(root, "show", "--no-patch", "--format=%P", review),
        )

        git(root, "switch", "--quiet", "main")
        git(
            root,
            "merge",
            "--quiet",
            "--no-ff",
            "-m",
            "merge rejected review",
            "all-rejected",
        )
        for retained in (proposal, review):
            self.assertEqual(
                0,
                git(
                    root,
                    "merge-base",
                    "--is-ancestor",
                    retained,
                    "HEAD",
                    check=False,
                ).returncode,
            )
        self.assertEqual(before, metadata_snapshot(root))
        stored = load_decision_cache(
            cache_path(root, "dump-research-info"),
            adapter="dump-research-info",
        )
        self.assertEqual(
            Disposition.REJECT,
            stored.decisions[plan.candidates[0].pid].disposition,
        )
        merged_head = git_output(root, "rev-parse", "HEAD")
        self.assertEqual(
            (),
            build_dump(root, source, merged_head, source_commit).candidates,
        )
        validate_joined_metadata(self, root)


if __name__ == "__main__":
    unittest.main()
