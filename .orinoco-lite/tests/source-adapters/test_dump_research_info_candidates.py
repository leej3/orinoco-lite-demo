from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

import yaml

from linkml_runtime.utils.schemaview import SchemaView
import orinoco_lite
from orinoco_lite.annotations import annotation_companion, assertion_sha256
from orinoco_lite.decisions import (
    Disposition,
    load_decision_cache,
    serialize_decision_cache,
    update_decision_cache,
)


ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = ROOT / "source-adapters/dump-research-info/metadata_adapter.py"
CANDIDATES_PATH = ROOT / "source-adapters/dump-research-info/candidates.py"
AGENT_PID = "xyzrins:source-adapters/dump-research-info/v2"
FIRST_AUTHOR_ROLE = {
    "broad_mappings": ["marcrel:aut"],
    "description": "The first of a set of authors associated with a publication.",
    "display_label": "First author",
    "pid": "obo:MS_1002034",
    "schema_type": "xyzri:XYZAgentRole",
}
SENIOR_AUTHOR_ROLE = {
    "broad_mappings": ["marcrel:aut"],
    "description": "The senior author associated with a publication.",
    "display_label": "Senior author",
    "pid": "obo:MS_1002035",
    "schema_type": "xyzri:XYZAgentRole",
}


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


SCHEMA = SchemaView(str(schema_fixture()))


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Cannot load test module {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


ADAPTER = load_module("dump_research_info_metadata_adapter_test", ADAPTER_PATH)
CANDIDATES = load_module("dump_research_info_candidates_test", CANDIDATES_PATH)


def git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments], text=True
    ).strip()


def init_repository(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.name", "Fixture Curator")
    git(root, "config", "user.email", "fixture@example.invalid")


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def write_source(
    root: Path,
    records: dict[str, list[dict[str, object]]],
    *,
    role_records: list[dict[str, object]] | None = None,
) -> str:
    source = root / "data/con_site"
    source.mkdir(parents=True, exist_ok=True)
    for class_name, values in records.items():
        (source / f"{class_name}.json").write_text(
            json.dumps(values, indent=2) + "\n", encoding="utf-8"
        )
    pool = root / "data/pool_psychoinformatics_de"
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "XYZAgentRole.json").write_text(
        json.dumps(role_records or [], indent=2) + "\n",
        encoding="utf-8",
    )
    return commit_all(root, "source")


def create_downstream(
    root: Path,
    records: list[tuple[str, dict[str, object]]],
    companions: list[tuple[str, dict[str, object]]] | None = None,
    *,
    include_adapter_agent: bool = True,
) -> str:
    init_repository(root)
    destination = root / "source-adapters/dump-research-info/metadata_adapter.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ADAPTER_PATH, destination)
    canonical_records = list(records)
    if include_adapter_agent:
        canonical_records.append(
            (
                "XYZInstrument/source-adapter-dump-research-info-v2.yaml",
                {
                    "pid": AGENT_PID,
                    "schema_type": "xyzri:XYZInstrument",
                    "title": "Synthetic dump adapter v2",
                },
            )
        )
    for relative, record in canonical_records:
        write_yaml(root / "metadata/records" / relative, record)
    if not canonical_records:
        (root / "metadata/records").mkdir(parents=True)
    for relative, companion in companions or []:
        write_yaml(root / "metadata/overlays/annotations" / relative, companion)
    return commit_all(root, "base")


def make_source(
    root: Path,
    records: dict[str, list[dict[str, object]]],
    *,
    role_records: list[dict[str, object]] | None = None,
) -> tuple[Path, str]:
    source = root / "source"
    init_repository(source)
    return source, write_source(source, records, role_records=role_records)


def build(
    downstream: Path,
    source: Path,
    metadata_base: str,
    source_commit: str,
    schema: SchemaView = SCHEMA,
    trusted_root: Path | None = None,
):
    return CANDIDATES.build_candidate_plan(
        downstream,
        source,
        metadata_base=metadata_base,
        expected_source_commit=source_commit,
        adapter_agent_pid=AGENT_PID,
        schema=schema,
        trusted_root=trusted_root,
    )


def pav_entry(
    path: str,
    assertion: dict[str, object],
    *,
    imported_by: str = AGENT_PID,
    imported_from: str = "https://example.invalid/source/record",
) -> dict[str, str]:
    return {
        "path": path,
        "assertion_sha256": assertion_sha256(assertion),
        "pav:importedBy": imported_by,
        "pav:importedFrom": imported_from,
    }


class SourceMappingTests(unittest.TestCase):
    def test_exact_identifier_and_unmatched_targets_retain_reviewed_policy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [
                    (
                        "XYZPublication/reviewed.yaml",
                        {
                            "pid": "xyzrins:publications/reviewed",
                            "schema_type": "xyzri:XYZPublication",
                            "identifiers": [
                                {
                                    "notation": "https://doi.org/10.1234/EXAMPLE",
                                    "schema_type": "dlthings:Identifier",
                                }
                            ],
                            "title": "Reviewed",
                        },
                    ),
                    (
                        "XYZPerson/person.yaml",
                        {
                            "pid": "xyzrins:persons/person",
                            "schema_type": "xyzri:XYZPerson",
                            "display_label": "Person",
                        },
                    ),
                ],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZPublication": [
                        {
                            "pid": "doi:10.1234/example",
                            "title": "Source",
                            "identifiers": [
                                {
                                    "notation": "10.1234/example",
                                    "schema_type": "dlthings:Identifier",
                                }
                            ],
                        }
                    ],
                    "XYZProject": [
                        {
                            "pid": "xyzrins:projects/new",
                            "title": "New",
                            "associated_with": [
                                {
                                    "object": "xyzrins:persons/person",
                                    "schema_type": "dlthings:Association",
                                }
                            ],
                        }
                    ],
                },
            )

            targets, coordinate = ADAPTER.build_source_targets(
                source,
                downstream,
                expected_source_commit=source_commit,
            )

            self.assertEqual(base, git(downstream, "rev-parse", "HEAD"))
            self.assertEqual(2, len(targets))
            publication = next(
                target for target in targets if target.source_class == "XYZPublication"
            )
            self.assertEqual("xyzrins:publications/reviewed", publication.target_pid)
            self.assertEqual("XYZPublication/reviewed.yaml", publication.record_path)
            project = next(
                target for target in targets if target.source_class == "XYZProject"
            )
            self.assertEqual("XYZProject/new.yaml", project.record_path)
            self.assertEqual(
                "xyzrins:persons/person",
                project.transformed_record["associated_with"][0]["object"],
            )
            self.assertEqual(source_commit, coordinate["commit"])
            self.assertNotIn("path", coordinate)

    def test_equal_duplicate_role_coalesces_to_the_full_authoritative_pool_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {
                    "XYZAgentRole": [deepcopy(FIRST_AUTHOR_ROLE)],
                    "XYZPublication": [
                        {
                            "attributed_to": [
                                {
                                    "object": "xyzrins:persons/author",
                                    "roles": ["obo:MS_1002034"],
                                    "schema_type": "dlthings:Attribution",
                                }
                            ],
                            "pid": "doi:10.1234/role-dependency",
                            "title": "Role dependency",
                        }
                    ],
                },
                role_records=[deepcopy(FIRST_AUTHOR_ROLE)],
            )

            targets, coordinate = ADAPTER.build_source_targets(
                source,
                downstream,
                expected_source_commit=source_commit,
            )

            self.assertEqual(2, len(targets))
            role_target = next(
                target for target in targets if target.source_class == "XYZAgentRole"
            )
            self.assertEqual(
                "data/pool_psychoinformatics_de",
                role_target.source_directory,
            )
            self.assertEqual(
                "data/pool_psychoinformatics_de:XYZAgentRole:obo:MS_1002034",
                role_target.source_record_id,
            )
            self.assertEqual(FIRST_AUTHOR_ROLE, role_target.transformed_record)
            self.assertEqual(
                {
                    "data/con_site": git(source, "rev-parse", "HEAD:data/con_site"),
                    "data/pool_psychoinformatics_de": git(
                        source,
                        "rev-parse",
                        "HEAD:data/pool_psychoinformatics_de",
                    ),
                },
                coordinate["source_roots"],
            )

            first = build(downstream, source, base, source_commit)
            self.assertEqual("2", first.adapter_version)
            self.assertEqual(CANDIDATES.PROVENANCE_IDENTITY, AGENT_PID)
            self.assertEqual(first.adapter_agent_pid, AGENT_PID)
            self.assertEqual(2, len(first.candidates))
            role = next(
                candidate
                for candidate in first.candidates
                if candidate.pid == "obo:MS_1002034"
            )
            self.assertEqual(
                "XYZAgentRole/obo-ms-1002034.yaml",
                role.record_path,
            )
            self.assertEqual("First author", role.proposed_record["display_label"])
            self.assertEqual(
                ["marcrel:aut"],
                role.proposed_record["broad_mappings"],
            )
            self.assertIsNotNone(role.proposed_companion)
            self.assertTrue(
                all(
                    "/blob/main/data/pool_psychoinformatics_de/" in entry["pav:importedFrom"]
                    for entry in role.proposed_companion["assertions"]
                )
            )
            publication = next(
                candidate
                for candidate in first.candidates
                if candidate.pid != "obo:MS_1002034"
            )
            self.assertTrue(
                all(
                    "/blob/main/data/con_site/" in entry["pav:importedFrom"]
                    for entry in publication.proposed_companion["assertions"]
                )
            )

            for change in first.file_changes():
                path = downstream / change.path
                self.assertIsNotNone(change.proposed)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(change.proposed)
            second = build(downstream, source, "2" * 40, source_commit)
            self.assertEqual((), second.candidates)
            self.assertEqual((), second.file_changes())

    def test_unequal_duplicate_role_across_source_roots_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {
                    "XYZAgentRole": [
                        {
                            "display_label": "Bounded duplicate",
                            "pid": "obo:MS_1002034",
                            "schema_type": "xyzri:XYZAgentRole",
                        }
                    ],
                    "XYZPublication": [
                        {
                            "attributed_to": [
                                {
                                    "object": "xyzrins:persons/author",
                                    "roles": ["obo:MS_1002034"],
                                    "schema_type": "dlthings:Attribution",
                                }
                            ],
                            "pid": "doi:10.1234/conflict",
                        }
                    ],
                },
                role_records=[deepcopy(FIRST_AUTHOR_ROLE)],
            )

            with self.assertRaisesRegex(
                ADAPTER.DumpResearchInfoAdapterError,
                "has conflicting records",
            ):
                ADAPTER.build_source_targets(
                    source,
                    downstream,
                    expected_source_commit=source_commit,
                )

    def test_required_role_must_exist_in_pool_even_when_already_canonical(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            create_downstream(
                downstream,
                [("XYZAgentRole/first-author.yaml", deepcopy(FIRST_AUTHOR_ROLE))],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZPublication": [
                        {
                            "attributed_to": [
                                {
                                    "object": "xyzrins:persons/author",
                                    "roles": ["obo:MS_1002034"],
                                    "schema_type": "dlthings:Attribution",
                                }
                            ],
                            "pid": "doi:10.1234/missing-authority",
                        }
                    ]
                },
            )

            with self.assertRaisesRegex(
                ADAPTER.DumpResearchInfoAdapterError,
                "has no authoritative XYZAgentRole record",
            ):
                ADAPTER.build_source_targets(
                    source,
                    downstream,
                    expected_source_commit=source_commit,
                )

    def test_exact_canonical_pool_role_is_validated_without_a_candidate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [("XYZAgentRole/first-author.yaml", deepcopy(FIRST_AUTHOR_ROLE))],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZPublication": [
                        {
                            "attributed_to": [
                                {
                                    "object": "xyzrins:persons/author",
                                    "roles": ["obo:MS_1002034"],
                                    "schema_type": "dlthings:Attribution",
                                }
                            ],
                            "pid": "doi:10.1234/already-canonical-role",
                        }
                    ]
                },
                role_records=[deepcopy(FIRST_AUTHOR_ROLE)],
            )

            plan = build(downstream, source, base, source_commit)

            self.assertEqual(
                {"xyzrins:publications/doi-10-1234-already-canonical-role"},
                {candidate.pid for candidate in plan.candidates},
            )
            self.assertNotIn(
                "obo:MS_1002034",
                {candidate.pid for candidate in plan.candidates},
            )

    def test_stale_same_pid_canonical_role_fails_as_a_prerequisite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            stale = deepcopy(FIRST_AUTHOR_ROLE)
            stale["display_label"] = "Stale first-author role"
            base = create_downstream(
                downstream,
                [("XYZAgentRole/first-author.yaml", stale)],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZPublication": [
                        {
                            "attributed_to": [
                                {
                                    "object": "xyzrins:persons/author",
                                    "roles": ["obo:MS_1002034"],
                                    "schema_type": "dlthings:Attribution",
                                }
                            ],
                            "pid": "doi:10.1234/stale-role",
                        }
                    ]
                },
                role_records=[deepcopy(FIRST_AUTHOR_ROLE)],
            )

            with self.assertRaisesRegex(
                CANDIDATES.DumpResearchInfoCandidateError,
                "authoritative role differs from the same-PID canonical role",
            ):
                build(downstream, source, base, source_commit)

    def test_authoritative_pool_role_does_not_alias_through_an_identifier(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            create_downstream(
                downstream,
                [
                    (
                        "XYZAgentRole/alias.yaml",
                        {
                            "display_label": "Local alias",
                            "identifiers": [
                                {
                                    "notation": "obo:MS_1002034",
                                    "schema_type": "dlthings:Identifier",
                                }
                            ],
                            "pid": "xyzrins:roles/local-alias",
                            "schema_type": "xyzri:XYZAgentRole",
                        },
                    )
                ],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZPublication": [
                        {
                            "attributed_to": [
                                {
                                    "object": "xyzrins:persons/author",
                                    "roles": ["obo:MS_1002034"],
                                    "schema_type": "dlthings:Attribution",
                                }
                            ],
                            "pid": "doi:10.1234/exact-role-identity",
                        }
                    ]
                },
                role_records=[deepcopy(FIRST_AUTHOR_ROLE)],
            )

            targets, _coordinate = ADAPTER.build_source_targets(
                source,
                downstream,
                expected_source_commit=source_commit,
            )

            role = next(
                target for target in targets if target.source_class == "XYZAgentRole"
            )
            self.assertEqual("obo:MS_1002034", role.target_pid)
            self.assertIsNone(role.baseline_record)
            self.assertEqual(
                "XYZAgentRole/obo-ms-1002034.yaml",
                role.record_path,
            )

    def test_unreferenced_primary_role_is_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {
                    "XYZAgentRole": [deepcopy(FIRST_AUTHOR_ROLE)],
                    "XYZProject": [
                        {
                            "pid": "xyzrins:projects/no-role-reference",
                            "title": "No role reference",
                        }
                    ],
                },
                role_records=[deepcopy(FIRST_AUTHOR_ROLE)],
            )

            targets, _coordinate = ADAPTER.build_source_targets(
                source,
                downstream,
                expected_source_commit=source_commit,
            )

            self.assertEqual(
                {"xyzrins:projects/no-role-reference"},
                {target.target_pid for target in targets},
            )

    def test_duplicate_pid_inside_the_pool_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/test"}]},
                role_records=[
                    deepcopy(FIRST_AUTHOR_ROLE),
                    deepcopy(FIRST_AUTHOR_ROLE),
                ],
            )

            with self.assertRaisesRegex(
                ADAPTER.DumpResearchInfoAdapterError,
                "Source PID is duplicated: obo:MS_1002034",
            ):
                ADAPTER.build_source_targets(
                    source,
                    downstream,
                    expected_source_commit=source_commit,
                )

    def test_source_record_order_does_not_change_dependency_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            publications = [
                {
                    "attributed_to": [
                        {
                            "object": f"xyzrins:persons/{name}",
                            "roles": [role["pid"]],
                            "schema_type": "dlthings:Attribution",
                        }
                    ],
                    "pid": f"doi:10.1234/{name}",
                    "title": name.title(),
                }
                for name, role in (
                    ("first", FIRST_AUTHOR_ROLE),
                    ("senior", SENIOR_AUTHOR_ROLE),
                )
            ]
            first_source, first_commit = make_source(
                root / "first",
                {"XYZPublication": publications},
                role_records=[
                    deepcopy(FIRST_AUTHOR_ROLE),
                    deepcopy(SENIOR_AUTHOR_ROLE),
                ],
            )
            second_source, second_commit = make_source(
                root / "second",
                {"XYZPublication": list(reversed(publications))},
                role_records=[
                    deepcopy(SENIOR_AUTHOR_ROLE),
                    deepcopy(FIRST_AUTHOR_ROLE),
                ],
            )

            first = build(downstream, first_source, base, first_commit)
            second = build(downstream, second_source, base, second_commit)

            self.assertEqual(first.candidates, second.candidates)
            self.assertNotEqual(first.source_coordinate, second.source_coordinate)

    def test_ambiguous_same_class_identifier_stops_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            create_downstream(
                downstream,
                [
                    (
                        f"XYZPublication/{name}.yaml",
                        {
                            "pid": f"xyzrins:publications/{name}",
                            "schema_type": "xyzri:XYZPublication",
                            "identifiers": [
                                {
                                    "notation": "10.1234/same",
                                    "schema_type": "dlthings:Identifier",
                                }
                            ],
                        },
                    )
                    for name in ("one", "two")
                ],
            )
            source, source_commit = make_source(
                root,
                {"XYZPublication": [{"pid": "doi:10.1234/same", "title": "Same"}]},
            )
            with self.assertRaisesRegex(
                ADAPTER.DumpResearchInfoAdapterError, "ambiguously matches"
            ):
                ADAPTER.build_source_targets(
                    source,
                    downstream,
                    expected_source_commit=source_commit,
                )

    def test_dirty_or_moved_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/test", "title": "Test"}]},
            )
            (source / "untracked").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(
                ADAPTER.DumpResearchInfoAdapterError, "must be clean"
            ):
                ADAPTER.build_source_targets(
                    source,
                    downstream,
                    expected_source_commit=source_commit,
                )
            (source / "untracked").unlink()
            with self.assertRaisesRegex(
                ADAPTER.DumpResearchInfoAdapterError, "checkout moved"
            ):
                ADAPTER.build_source_targets(
                    source,
                    downstream,
                    expected_source_commit="0" * 40,
                )


class CandidatePlanTests(unittest.TestCase):
    def test_trusted_adapter_is_separate_from_the_metadata_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "New"}]},
            )
            trusted = root / "trusted"
            trusted_adapter = (
                trusted / "source-adapters/dump-research-info/metadata_adapter.py"
            )
            trusted_adapter.parent.mkdir(parents=True)
            shutil.copyfile(ADAPTER_PATH, trusted_adapter)
            untrusted_adapter = (
                downstream / "source-adapters/dump-research-info/metadata_adapter.py"
            )
            untrusted_adapter.write_text(
                "raise RuntimeError('untrusted adapter executed')\n",
                encoding="utf-8",
            )

            plan = build(
                downstream,
                source,
                base,
                source_commit,
                trusted_root=trusted,
            )

            self.assertEqual(1, len(plan.candidates))
            self.assertEqual(
                {
                    "commit": source_commit,
                    "repository": "https://github.com/con/dump-research-info",
                    "source_roots": {
                        "data/con_site": git(
                            source, "rev-parse", "HEAD:data/con_site"
                        ),
                        "data/pool_psychoinformatics_de": git(
                            source,
                            "rev-parse",
                            "HEAD:data/pool_psychoinformatics_de",
                        ),
                    },
                },
                dict(plan.source_coordinate),
            )

    def test_provenance_identity_must_name_a_reviewed_instrument(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [],
                include_adapter_agent=False,
            )
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "New"}]},
            )

            with self.assertRaisesRegex(
                CANDIDATES.DumpResearchInfoCandidateError,
                "must identify a reviewed xyzri:XYZInstrument record",
            ):
                build(downstream, source, base, source_commit)

    def test_provenance_identity_must_match_the_current_adapter_version(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "New"}]},
            )

            with self.assertRaisesRegex(
                CANDIDATES.DumpResearchInfoCandidateError,
                "must equal the reviewed identity for adapter version 2",
            ):
                CANDIDATES.build_candidate_plan(
                    downstream,
                    source,
                    metadata_base=base,
                    expected_source_commit=source_commit,
                    adapter_agent_pid=(
                        "xyzrins:source-adapters/dump-research-info/v1"
                    ),
                    schema=SCHEMA,
                )

    def test_provenance_identity_rejects_a_non_instrument_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [],
                include_adapter_agent=False,
            )
            write_yaml(
                downstream / "metadata/records/XYZProject/wrong-agent.yaml",
                {
                    "pid": AGENT_PID,
                    "schema_type": "xyzri:XYZProject",
                    "title": "Wrong adapter identity type",
                },
            )
            base = commit_all(downstream, "wrong adapter identity type")
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "New"}]},
            )

            with self.assertRaisesRegex(
                CANDIDATES.DumpResearchInfoCandidateError,
                "must identify a reviewed xyzri:XYZInstrument record",
            ):
                build(downstream, source, base, source_commit)

    def test_candidate_construction_is_host_neutral_about_downstream_git(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "New"}]},
            )
            (downstream / "unrelated-untracked").write_text(
                "host state", encoding="utf-8"
            )

            plan = build(downstream, source, "f" * 40, source_commit)

            self.assertEqual("f" * 40, plan.metadata_base)
            self.assertEqual(1, len(plan.candidates))

    def test_new_record_uses_stored_qualified_assertions_and_pav_only_overlay(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {
                    "XYZProject": [
                        {
                            "pid": "xyzrins:projects/new",
                            "title": "New project",
                            "about": ["xyzrins:topics/open-science"],
                            "associated_with": [
                                {
                                    "object": "xyzrins:persons/curator",
                                    "schema_type": "dlthings:Association",
                                }
                            ],
                            "identifiers": [
                                {
                                    "notation": "new-project",
                                    "schema_type": "dlthings:Identifier",
                                }
                            ],
                        }
                    ]
                },
            )

            plan = build(downstream, source, base, source_commit)

            self.assertEqual(1, len(plan.candidates))
            candidate = plan.candidates[0]
            proposed = dict(candidate.proposed_record)
            self.assertEqual("New project", proposed["title"])
            title_assertion = next(
                item
                for item in proposed["attributes"]
                if item["predicate"] == "dlthings:title"
            )
            self.assertEqual(
                "dlthings:AttributeSpecification", title_assertion["schema_type"]
            )
            self.assertNotIn("annotations", title_assertion)
            self.assertEqual(
                {
                    "object": "xyzrins:topics/open-science",
                    "predicate": "dcterms:subject",
                },
                proposed["characterized_by"][0],
            )
            self.assertNotIn("schema_type", proposed["characterized_by"][0])
            self.assertNotIn("annotations", proposed["identifiers"][0])
            companion = dict(candidate.proposed_companion)
            self.assertEqual(
                [
                    "/associated_with",
                    "/attributes",
                    "/characterized_by",
                    "/identifiers",
                ],
                [item["path"] for item in companion["assertions"]],
            )
            self.assertTrue(
                all(
                    item["pav:importedBy"] == AGENT_PID
                    for item in companion["assertions"]
                )
            )
            self.assertTrue(
                all(
                    "/blob/main/" in item["pav:importedFrom"]
                    for item in companion["assertions"]
                )
            )
            self.assertEqual(
                {
                    "metadata/records/XYZProject/new.yaml",
                    "metadata/overlays/annotations/XYZProject/new.yaml",
                },
                {change.path for change in candidate.file_changes()},
            )

    def test_populated_topical_is_preserved_while_source_assertion_is_added(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [
                    (
                        "XYZProject/item.yaml",
                        {
                            "pid": "xyzrins:projects/item",
                            "schema_type": "xyzri:XYZProject",
                            "title": "Human title",
                        },
                    )
                ],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZProject": [
                        {"pid": "xyzrins:projects/item", "title": "Source title"}
                    ]
                },
            )

            candidate = build(downstream, source, base, source_commit).candidates[0]
            proposed = dict(candidate.proposed_record)
            self.assertEqual("Human title", proposed["title"])
            self.assertEqual("Source title", proposed["attributes"][0]["value"])
            self.assertEqual(
                "/attributes",
                candidate.proposed_companion["assertions"][0]["path"],
            )

    def test_explicit_source_attributes_use_upstream_multivalue_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {
                    "XYZPerson": [
                        {
                            "pid": "xyzrins:persons/new",
                            "display_label": "New person",
                            "attributes": [
                                {
                                    "predicate": "owl:sameAs",
                                    "schema_type": "dlthings:AttributeSpecification",
                                    "value": "https://example.org/one",
                                },
                                {
                                    "predicate": "owl:sameAs",
                                    "schema_type": "dlthings:AttributeSpecification",
                                    "value": "https://example.org/two",
                                },
                            ],
                        }
                    ]
                },
            )

            candidate = build(downstream, source, base, source_commit).candidates[0]
            same_as = [
                assertion
                for assertion in candidate.proposed_record["attributes"]
                if assertion["predicate"] == "owl:sameAs"
            ]

            self.assertEqual(
                ["https://example.org/one", "https://example.org/two"],
                [assertion["value"] for assertion in same_as],
            )
            self.assertTrue(
                all(
                    assertion["schema_type"] == "dlthings:AttributeSpecification"
                    for assertion in same_as
                )
            )
            self.assertEqual(
                2,
                sum(
                    entry["path"] == "/attributes"
                    and entry["assertion_sha256"]
                    in {assertion_sha256(assertion) for assertion in same_as}
                    for entry in candidate.proposed_companion["assertions"]
                ),
            )

    def test_missing_topical_copies_equivalent_unowned_assertion_without_pav(
        self,
    ) -> None:
        assertion = {
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Source title",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [
                    (
                        "XYZProject/item.yaml",
                        {
                            "pid": "xyzrins:projects/item",
                            "schema_type": "xyzri:XYZProject",
                            "attributes": [assertion],
                        },
                    )
                ],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZProject": [
                        {"pid": "xyzrins:projects/item", "title": "Source title"}
                    ]
                },
            )

            candidate = build(downstream, source, base, source_commit).candidates[0]

            self.assertEqual("Source title", candidate.proposed_record["title"])
            self.assertEqual([assertion], candidate.proposed_record["attributes"])
            self.assertIsNone(candidate.proposed_companion)

    def test_equivalent_richer_unowned_assertion_produces_no_provenance_only_diff(
        self,
    ) -> None:
        assertion = {
            "description": "Human detail",
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Source title",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [
                    (
                        "XYZProject/item.yaml",
                        {
                            "pid": "xyzrins:projects/item",
                            "schema_type": "xyzri:XYZProject",
                            "title": "Source title",
                            "attributes": [assertion],
                        },
                    )
                ],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZProject": [
                        {"pid": "xyzrins:projects/item", "title": "Source title"}
                    ]
                },
            )

            plan = build(downstream, source, base, source_commit)

            self.assertEqual((), plan.candidates)

    def test_changed_same_owner_replaces_only_its_qualified_assertion(self) -> None:
        old = {
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Old source title",
        }
        human = {
            "predicate": "dcterms:description",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Human detail",
        }
        relative = "XYZProject/item.yaml"
        existing_companion = annotation_companion(
            "xyzrins:projects/item", [pav_entry("/attributes", old)]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [
                    (
                        relative,
                        {
                            "pid": "xyzrins:projects/item",
                            "schema_type": "xyzri:XYZProject",
                            "title": "Human title",
                            "attributes": [human, old],
                        },
                    )
                ],
                [(relative, existing_companion)],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZProject": [
                        {"pid": "xyzrins:projects/item", "title": "New source title"}
                    ]
                },
            )

            candidate = build(downstream, source, base, source_commit).candidates[0]
            proposed = dict(candidate.proposed_record)
            self.assertEqual("Human title", proposed["title"])
            self.assertEqual(
                ["Human detail", "New source title"],
                [item["value"] for item in proposed["attributes"]],
            )
            self.assertEqual(1, len(candidate.proposed_companion["assertions"]))
            self.assertEqual(
                assertion_sha256(proposed["attributes"][1]),
                candidate.proposed_companion["assertions"][0]["assertion_sha256"],
            )

    def test_removed_source_assertions_clear_only_same_owner_content(self) -> None:
        same_title = {
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Old machine title",
        }
        human_title = {
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Human title assertion",
        }
        other_title = {
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Other adapter title",
        }
        same_homepage = {
            "predicate": "foaf:homepage",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "https://old-machine.example/",
        }
        human_homepage = {
            "predicate": "foaf:homepage",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "https://human.example/",
        }
        other_homepage = {
            "predicate": "foaf:homepage",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "https://other-adapter.example/",
        }
        same_about = {
            "object": "xyzrins:topics/old-machine",
            "predicate": "dcterms:subject",
        }
        human_about = {
            "object": "xyzrins:topics/human",
            "predicate": "dcterms:subject",
        }
        other_about = {
            "object": "xyzrins:topics/other-adapter",
            "predicate": "dcterms:subject",
        }
        same_association = {
            "object": "xyzrins:persons/old-machine",
            "schema_type": "dlthings:Association",
        }
        human_association = {
            "object": "xyzrins:persons/human",
            "schema_type": "dlthings:Association",
        }
        other_association = {
            "object": "xyzrins:persons/other-adapter",
            "schema_type": "dlthings:Association",
        }
        relative = "XYZProject/item.yaml"
        other_agent = "urn:example:test:other-adapter:v1"
        companion = annotation_companion(
            "xyzrins:projects/item",
            [
                pav_entry("/attributes", same_title),
                pav_entry(
                    "/attributes",
                    other_title,
                    imported_by=other_agent,
                ),
                pav_entry("/attributes", same_homepage),
                pav_entry(
                    "/attributes",
                    other_homepage,
                    imported_by=other_agent,
                ),
                pav_entry("/characterized_by", same_about),
                pav_entry(
                    "/characterized_by",
                    other_about,
                    imported_by=other_agent,
                ),
                pav_entry("/associated_with", same_association),
                pav_entry(
                    "/associated_with",
                    other_association,
                    imported_by=other_agent,
                ),
            ],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [
                    (
                        relative,
                        {
                            "pid": "xyzrins:projects/item",
                            "schema_type": "xyzri:XYZProject",
                            "title": "Curated top-level title",
                            "about": ["xyzrins:topics/curated-topical"],
                            "attributes": [
                                same_title,
                                human_title,
                                other_title,
                                same_homepage,
                                human_homepage,
                                other_homepage,
                            ],
                            "characterized_by": [
                                same_about,
                                human_about,
                                other_about,
                            ],
                            "associated_with": [
                                same_association,
                                human_association,
                                other_association,
                            ],
                        },
                    )
                ],
                [(relative, companion)],
            )
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/item"}]},
            )

            candidate = build(downstream, source, base, source_commit).candidates[0]
            proposed = candidate.proposed_record

            self.assertEqual("Curated top-level title", proposed["title"])
            self.assertEqual(
                ["xyzrins:topics/curated-topical"],
                proposed["about"],
            )
            self.assertEqual(
                [human_title, other_title, human_homepage, other_homepage],
                proposed["attributes"],
            )
            self.assertEqual(
                [human_about, other_about],
                proposed["characterized_by"],
            )
            self.assertEqual(
                [human_association, other_association],
                proposed["associated_with"],
            )
            self.assertEqual(
                {other_agent},
                {
                    entry["pav:importedBy"]
                    for entry in candidate.proposed_companion["assertions"]
                },
            )
            self.assertEqual(4, len(candidate.proposed_companion["assertions"]))

    def test_foreign_owned_assertion_coexists_with_new_adapter_assertion(self) -> None:
        foreign = {
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Foreign source title",
        }
        relative = "XYZProject/item.yaml"
        foreign_agent = "urn:example:test:foreign-adapter:v1"
        companion = annotation_companion(
            "xyzrins:projects/item",
            [pav_entry("/attributes", foreign, imported_by=foreign_agent)],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [
                    (
                        relative,
                        {
                            "pid": "xyzrins:projects/item",
                            "schema_type": "xyzri:XYZProject",
                            "title": "Human title",
                            "attributes": [foreign],
                        },
                    )
                ],
                [(relative, companion)],
            )
            source, source_commit = make_source(
                root,
                {
                    "XYZProject": [
                        {"pid": "xyzrins:projects/item", "title": "Our source title"}
                    ]
                },
            )

            candidate = build(downstream, source, base, source_commit).candidates[0]

            self.assertEqual(
                ["Foreign source title", "Our source title"],
                [item["value"] for item in candidate.proposed_record["attributes"]],
            )
            self.assertEqual(
                {foreign_agent, AGENT_PID},
                {
                    item["pav:importedBy"]
                    for item in candidate.proposed_companion["assertions"]
                },
            )

    def test_rejection_suppresses_unchanged_claim_and_material_change_reopens(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "One"}]},
            )
            initial = build(downstream, source, base, source_commit)
            candidate = initial.candidates[0]
            cache = load_decision_cache(
                downstream / CANDIDATES.DECISION_CACHE,
                adapter=CANDIDATES.ADAPTER,
            )
            cache = update_decision_cache(
                cache,
                initial,
                {candidate.pid: Disposition.REJECT},
                review_ref="github-comment:42",
                source_coordinate=initial.source_coordinate,
                reviewer="https://github.com/fixture-curator",
                reviewed_at="2026-08-24T12:00:00Z",
                review_url="https://github.com/con/example/pull/1#issuecomment-42",
            )
            cache_path = downstream / CANDIDATES.DECISION_CACHE
            cache_path.parent.mkdir(parents=True)
            cache_path.write_bytes(serialize_decision_cache(cache))

            self.assertEqual(
                (), build(downstream, source, base, source_commit).candidates
            )

            source_file = source / "data/con_site/XYZProject.json"
            changed = json.loads(source_file.read_text(encoding="utf-8"))
            changed[0]["title"] = "Two"
            source_file.write_text(
                json.dumps(changed, indent=2) + "\n", encoding="utf-8"
            )
            changed_commit = commit_all(source, "material change")
            reopened = build(downstream, source, base, changed_commit)
            self.assertEqual(1, len(reopened.candidates))
            self.assertNotEqual(
                candidate.claim_sha256, reopened.candidates[0].claim_sha256
            )

    def test_commit_outside_source_tree_keeps_cached_claims_suppressed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {
                    "XYZProject": [
                        {
                            "pid": "xyzrins:projects/accepted",
                            "title": "Accepted",
                        },
                        {
                            "pid": "xyzrins:projects/rejected",
                            "title": "Rejected",
                        },
                    ]
                },
            )
            initial = build(downstream, source, base, source_commit)
            dispositions = {
                "xyzrins:projects/accepted": Disposition.ACCEPT,
                "xyzrins:projects/rejected": Disposition.REJECT,
            }
            cache = update_decision_cache(
                load_decision_cache(
                    downstream / CANDIDATES.DECISION_CACHE,
                    adapter=CANDIDATES.ADAPTER,
                ),
                initial,
                dispositions,
                review_ref="github-comment:43",
                source_coordinate=initial.source_coordinate,
                reviewer="https://github.com/fixture-curator",
                reviewed_at="2026-08-24T12:01:00Z",
                review_url="https://github.com/con/example/pull/1#issuecomment-43",
            )

            (source / "README.md").write_text(
                "Transport-only source revision.\n", encoding="utf-8"
            )
            transport_commit = commit_all(source, "transport-only change")
            transported = build(downstream, source, base, transport_commit)

            self.assertNotEqual(
                initial.source_coordinate["commit"],
                transported.source_coordinate["commit"],
            )
            self.assertEqual(
                initial.source_coordinate["source_roots"],
                transported.source_coordinate["source_roots"],
            )
            initial_by_pid = {
                candidate.pid: candidate for candidate in initial.candidates
            }
            transported_by_pid = {
                candidate.pid: candidate for candidate in transported.candidates
            }
            self.assertEqual(set(dispositions), set(initial_by_pid))
            self.assertEqual(set(initial_by_pid), set(transported_by_pid))
            for pid, candidate in initial_by_pid.items():
                updated = transported_by_pid[pid]
                self.assertEqual(candidate.source_namespace, updated.source_namespace)
                self.assertEqual(candidate.source_record_id, updated.source_record_id)
                self.assertEqual(candidate.pid, updated.pid)
                self.assertEqual(candidate.record_path, updated.record_path)
                self.assertEqual(candidate.source_claim, updated.source_claim)
                self.assertEqual(candidate.claim_sha256, updated.claim_sha256)

            cache_path = downstream / CANDIDATES.DECISION_CACHE
            cache_path.parent.mkdir(parents=True)
            cache_path.write_bytes(serialize_decision_cache(cache))
            filtered = build(downstream, source, base, transport_commit)
            self.assertEqual(filtered.candidates, ())
            self.assertEqual(filtered.file_changes(), ())

    def test_mapping_predicate_change_reopens_the_semantic_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "One"}]},
            )
            initial = build(downstream, source, base, source_commit)
            candidate = initial.candidates[0]
            cache = update_decision_cache(
                load_decision_cache(
                    downstream / CANDIDATES.DECISION_CACHE,
                    adapter=CANDIDATES.ADAPTER,
                ),
                initial,
                {candidate.pid: Disposition.REJECT},
                review_ref="github-comment:42",
                source_coordinate=initial.source_coordinate,
                reviewer="https://github.com/fixture-curator",
                reviewed_at="2026-08-24T12:00:00Z",
                review_url="https://github.com/con/example/pull/1#issuecomment-42",
            )
            cache_path = downstream / CANDIDATES.DECISION_CACHE
            cache_path.parent.mkdir(parents=True)
            cache_path.write_bytes(serialize_decision_cache(cache))

            original_get_uri = SCHEMA.get_uri

            def changed_uri(element, *args, **kwargs):
                if element == "title":
                    return "dcterms:title"
                return original_get_uri(element, *args, **kwargs)

            with patch.object(SCHEMA, "get_uri", side_effect=changed_uri):
                reopened = build(downstream, source, base, source_commit)

            self.assertEqual(1, len(reopened.candidates))
            self.assertNotEqual(
                candidate.claim_sha256,
                reopened.candidates[0].claim_sha256,
            )
            self.assertEqual(
                "dcterms:title",
                reopened.candidates[0].source_claim["attributes"][0]["predicate"],
            )

    def test_repeat_and_relocated_source_are_deterministic_and_absence_is_not_deletion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(
                downstream,
                [
                    (
                        "XYZPerson/site-only.yaml",
                        {
                            "pid": "xyzrins:persons/site-only",
                            "schema_type": "xyzri:XYZPerson",
                            "display_label": "Site only",
                        },
                    )
                ],
            )
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "New"}]},
            )
            first = build(downstream, source, base, source_commit)
            second = build(downstream, source, base, source_commit)
            relocated = root / "relocated"
            subprocess.run(
                ["git", "clone", "-q", str(source), str(relocated)], check=True
            )
            third = build(downstream, relocated, base, source_commit)

            self.assertEqual(first, second)
            self.assertEqual(first, third)
            self.assertEqual(
                {"xyzrins:projects/new"}, {item.pid for item in first.candidates}
            )

    def test_applied_plan_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {"XYZProject": [{"pid": "xyzrins:projects/new", "title": "New"}]},
            )
            first = build(downstream, source, base, source_commit)
            self.assertEqual(1, len(first.candidates))
            for change in first.file_changes():
                path = downstream / change.path
                self.assertIsNotNone(change.proposed)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(change.proposed)

            second = build(downstream, source, "2" * 40, source_commit)

            self.assertEqual(second.candidates, ())
            self.assertEqual(second.file_changes(), ())

    def test_unknown_source_field_is_not_silently_generalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downstream = root / "downstream"
            base = create_downstream(downstream, [])
            source, source_commit = make_source(
                root,
                {
                    "XYZProject": [
                        {
                            "pid": "xyzrins:projects/new",
                            "title": "New",
                            "unreviewed_field": "value",
                        }
                    ]
                },
            )
            with self.assertRaisesRegex(
                CANDIDATES.DumpResearchInfoCandidateError,
                "unsupported source fields: unreviewed_field",
            ):
                build(downstream, source, base, source_commit)


if __name__ == "__main__":
    unittest.main()
