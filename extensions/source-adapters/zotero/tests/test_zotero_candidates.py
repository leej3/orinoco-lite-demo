from __future__ import annotations

from collections import Counter
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

from linkml_runtime.utils.schemaview import SchemaView
import orinoco_lite
from orinoco_lite.annotations import annotation_companion, assertion_sha256
from orinoco_lite.candidates import Candidate, CandidatePlan
from orinoco_lite.canonical import canonical_yaml_bytes
from orinoco_lite.decisions import (
    DecisionCache,
    Disposition,
    serialize_decision_cache,
)
import yaml


ROOT = Path(__file__).resolve().parents[4]
AGENT_PID = "xyzrins:source-adapters/zotero/v1"
REVIEWED_ADAPTER_AGENT_PIDS = (
    "xyzrins:source-adapters/dump-research-info/v1",
    "xyzrins:source-adapters/dump-research-info/v2",
    "xyzrins:source-adapters/zotero/v1",
)
METADATA_BASE = "1" * 40


def schema_fixture() -> Path:
    configured = os.environ.get("ORINOCO_CANDIDATE_RESOURCE_ROOT")
    resources = Path(configured) if configured else Path(orinoco_lite.__file__).parent / "_resources"
    schema = resources / "schema/demo-research-information/unreleased.yaml"
    if not schema.is_file():
        raise RuntimeError("The installed package's pinned Things Schema is unavailable")
    return schema


SCHEMA = SchemaView(str(schema_fixture()))


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


fixture_metadata = load_module(
    "orinoco_zotero_fixture_metadata_tests",
    Path(__file__).with_name("fixture_metadata.py"),
)
neutralize_reviewed_adapter_state = (
    fixture_metadata.neutralize_reviewed_adapter_state
)
provider = load_module(
    "orinoco_zotero_candidates_tests",
    ROOT / "extensions/source-adapters/zotero/candidates.py",
)


def copy_zotero_fixture(source: Path, destination: Path) -> None:
    shutil.copytree(
        source / "extensions/source-adapters/zotero",
        destination / "extensions/source-adapters/zotero",
    )
    shutil.copytree(
        source / "site-specific/sources/zotero",
        destination / "site-specific/sources/zotero",
    )


def prepared_root(destination: Path) -> Path:
    root = destination / "consumer"
    copy_zotero_fixture(ROOT, root)
    shutil.copyfile(ROOT / "orinoco.yaml", root / "orinoco.yaml")
    shutil.copyfile(ROOT / "site-specific/site.yaml", root / "site-specific/site.yaml")
    shutil.copytree(ROOT / "site-specific/metadata/records", root / "site-specific/metadata/records")
    annotations = ROOT / "site-specific/metadata/overlays/annotations"
    if annotations.is_dir():
        shutil.copytree(annotations, root / "site-specific/metadata/overlays/annotations")
    neutralize_reviewed_adapter_state(
        root,
        adapter_agent_pids=REVIEWED_ADAPTER_AGENT_PIDS,
        decision_caches=(
            Path("site-specific/curation-records/zotero.yaml"),
        ),
    )
    (root / "site-specific/curation-records").mkdir(parents=True, exist_ok=True)
    for path in sorted((root / "site-specific/metadata/records").rglob("*.yaml")):
        if path.name == ".dumpthings.yaml":
            continue
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        path.write_bytes(canonical_yaml_bytes(value))
    return root


def metadata_snapshot(root: Path) -> dict[Path, bytes]:
    metadata = root / "site-specific/metadata"
    return {
        path.relative_to(metadata): path.read_bytes()
        for path in sorted(metadata.rglob("*.yaml"))
    }


def build(
    root: Path,
    name: str = "proposal",
    *,
    adapter_agent_pid: str = AGENT_PID,
    metadata_base: str = METADATA_BASE,
    trusted_root: Path | None = None,
) -> CandidatePlan:
    with patch.dict(
        os.environ,
        {
            "ORINOCO_ROOT": str(root.resolve()),
            "ORINOCO_RECORDS_ROOT": str(
                (root / "site-specific/metadata/records").resolve()
            ),
        },
    ):
        return provider.build_candidate_plan(
            root,
            root / f"build/{name}",
            metadata_base=metadata_base,
            expected_library_version=668,
            adapter_agent_pid=adapter_agent_pid,
            schema=SCHEMA,
            trusted_root=trusted_root,
        )


def assert_no_machine_pav(test: unittest.TestCase, value: object) -> None:
    if isinstance(value, dict):
        test.assertNotIn("pav:importedBy", value)
        test.assertNotIn("pav:importedFrom", value)
        for child in value.values():
            assert_no_machine_pav(test, child)
    elif isinstance(value, list):
        for child in value:
            assert_no_machine_pav(test, child)


def pav_entry(
    path: str,
    assertion: dict[str, object],
    *,
    imported_by: str = AGENT_PID,
) -> dict[str, str]:
    return {
        "path": path,
        "assertion_sha256": assertion_sha256(assertion),
        "pav:importedBy": imported_by,
        "pav:importedFrom": "https://example.invalid/source/previous",
    }


class FrozenZoteroCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = prepared_root(Path(cls.temporary.name))
        cls.before = metadata_snapshot(cls.root)
        cls.plan = build(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_frozen_source_builds_complete_deterministic_plan(self) -> None:
        second = build(self.root, "second")

        self.assertEqual(second, self.plan)
        self.assertEqual(metadata_snapshot(self.root), self.before)
        self.assertEqual(self.plan.adapter, "zotero")
        self.assertEqual(self.plan.adapter_version, "1")
        source_config = yaml.safe_load(
            (
                self.root / "site-specific/sources/zotero/source.yaml"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(source_config["provenance_identity"], AGENT_PID)
        self.assertEqual(self.plan.adapter_agent_pid, AGENT_PID)
        self.assertEqual(self.plan.metadata_base, METADATA_BASE)
        self.assertEqual(
            dict(self.plan.source_coordinate),
            {
                "content_sha256": (
                    "sha256:"
                    "23aa443a248e9e1dfc73003cde76f3a93c533bf9e57cc5674539b80da52f17b8"
                ),
                "group_id": 6197458,
                "kind": "zotero-public-library",
                "library_version": 668,
            },
        )
        self.assertEqual(len(self.plan.candidates), 126)
        self.assertEqual(len(self.plan.file_changes()), 252)
        self.assertTrue(
            all(
                candidate.operation.value == "modify"
                for candidate in self.plan.candidates
            )
        )
        self.assertTrue(
            all(not candidate.blockers for candidate in self.plan.candidates)
        )

        assertions = [
            assertion
            for candidate in self.plan.candidates
            for assertion in candidate.proposed_companion["assertions"]
        ]
        self.assertEqual(len(assertions), 429)
        self.assertEqual(
            {assertion["path"] for assertion in assertions},
            {"/attributes", "/attributed_to", "/characterized_by"},
        )
        self.assertTrue(
            all(assertion["pav:importedBy"] == AGENT_PID for assertion in assertions)
        )

        for candidate in self.plan.candidates:
            assert_no_machine_pav(self, dict(candidate.proposed_record))
            baseline = candidate.baseline_record
            proposed = candidate.proposed_record
            assert baseline is not None
            for field in ("title", "display_label", "description", "kind", "about"):
                self.assertEqual(proposed.get(field), baseline.get(field))

        attribution_counts = Counter(
            attribution["object"]
            for candidate in self.plan.candidates
            for attribution in candidate.proposed_record.get("attributed_to", [])
            if attribution["object"]
            in {
                "xyzrins:persons/brock-wester",
                "xyzrins:persons/russell-poldrack",
            }
        )
        self.assertEqual(
            attribution_counts,
            Counter(
                {
                    "xyzrins:persons/brock-wester": 2,
                    "xyzrins:persons/russell-poldrack": 19,
                }
            ),
        )

    def test_trusted_code_and_source_are_separate_from_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            root = prepared_root(temporary / "metadata-base")
            trusted = temporary / "trusted"
            copy_zotero_fixture(ROOT, trusted)
            adapter_root = root / "extensions/source-adapters/zotero"
            (adapter_root / "metadata_adapter.py").write_text(
                "raise RuntimeError('untrusted adapter executed')\n",
                encoding="utf-8",
            )
            source_root = root / "site-specific/sources/zotero"
            (source_root / "content/snapshot.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            (source_root / "evidence/candidates/XYZPublication.json").write_text(
                "[]\n",
                encoding="utf-8",
            )
            (source_root / "policy/site-policy.yaml").write_text(
                "invalid: untrusted policy\n",
                encoding="utf-8",
            )
            (adapter_root / "zotero_ingest.py").unlink()
            (adapter_root / "zotero_site_export.py").unlink()

            plan = build(root, "trusted-boundary", trusted_root=trusted)

            self.assertEqual(plan, self.plan)
            self.assertEqual(
                dict(plan.source_coordinate), dict(self.plan.source_coordinate)
            )
            self.assertEqual(126, len(plan.candidates))
            self.assertTrue(
                (root / "build/trusted-boundary/zotero-site-publications").is_dir()
            )
            self.assertFalse((trusted / "build").exists())

    def test_compact_decisions_suppress_and_reopen(self) -> None:
        dispositions = {
            candidate.pid: Disposition.ACCEPT for candidate in self.plan.candidates
        }
        cache = DecisionCache.empty("zotero").updated(
            self.plan,
            dispositions,
            review_ref="github-comment:123",
            source_coordinate=self.plan.source_coordinate,
            reviewer="https://github.com/reviewer",
            reviewed_at="2026-08-24T12:00:00Z",
            review_url=(
                "https://github.com/ORINOCO-Lite/test-orinoco-downstream-website/"
                "pull/1#issuecomment-123"
            ),
        )
        self.assertEqual(cache.candidates_requiring_review(self.plan), ())

        coordinate_only = CandidatePlan(
            adapter=self.plan.adapter,
            adapter_version=self.plan.adapter_version,
            adapter_agent_pid=self.plan.adapter_agent_pid,
            source_namespace=self.plan.source_namespace,
            source_coordinate={
                **dict(self.plan.source_coordinate),
                "library_version": 452,
            },
            metadata_base=self.plan.metadata_base,
            candidates=self.plan.candidates,
        )
        self.assertEqual(cache.candidates_requiring_review(coordinate_only), ())

        original = self.plan.candidates[0]
        changed_claim = dict(original.source_claim)
        changed_claim["title"] = f"{changed_claim['title']} (source revision)"
        changed = Candidate(
            source_namespace=original.source_namespace,
            source_record_id=original.source_record_id,
            pid=original.pid,
            record_path=original.record_path,
            baseline_record=original.baseline_record,
            proposed_record=original.proposed_record,
            baseline_companion=original.baseline_companion,
            proposed_companion=original.proposed_companion,
            source_claim=changed_claim,
        )
        changed_plan = CandidatePlan(
            adapter=self.plan.adapter,
            adapter_version=self.plan.adapter_version,
            adapter_agent_pid=self.plan.adapter_agent_pid,
            source_namespace=self.plan.source_namespace,
            source_coordinate=self.plan.source_coordinate,
            metadata_base=self.plan.metadata_base,
            candidates=(changed,),
        )
        self.assertEqual(cache.candidates_requiring_review(changed_plan), (changed,))

    def test_applied_plan_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepared_root(Path(directory))
            first = build(root)
            for change in first.file_changes():
                path = root / change.path
                self.assertIsNotNone(change.proposed)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(change.proposed)
            second = build(root, "rerun", metadata_base="2" * 40)
            self.assertEqual(second.candidates, ())
            self.assertEqual(second.file_changes(), ())

    def test_builder_filters_current_compact_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepared_root(Path(directory))
            plan = build(root)
            cache = DecisionCache.empty("zotero").updated(
                plan,
                {candidate.pid: Disposition.REJECT for candidate in plan.candidates},
                review_ref="github-comment:124",
                source_coordinate=plan.source_coordinate,
                reviewer="https://github.com/reviewer",
                reviewed_at="2026-08-24T12:01:00Z",
                review_url=(
                    "https://github.com/ORINOCO-Lite/test-orinoco-downstream-website/"
                    "pull/1#issuecomment-124"
                ),
            )
            cache_path = root / "site-specific/curation-records/zotero.yaml"
            cache_path.write_bytes(serialize_decision_cache(cache))

            filtered = build(root, "filtered")
            self.assertEqual(filtered.candidates, ())
            self.assertEqual(filtered.file_changes(), ())

    def test_fetched_at_only_change_keeps_cached_claims_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            root = prepared_root(temporary / "metadata-base")
            trusted = temporary / "trusted"
            copy_zotero_fixture(ROOT, trusted)
            initial = build(root, "transport-initial", trusted_root=trusted)
            dispositions = {
                candidate.pid: (
                    Disposition.ACCEPT if index % 2 == 0 else Disposition.REJECT
                )
                for index, candidate in enumerate(initial.candidates)
            }
            self.assertEqual(
                {Disposition.ACCEPT, Disposition.REJECT}, set(dispositions.values())
            )
            cache = DecisionCache.empty("zotero").updated(
                initial,
                dispositions,
                review_ref="github-comment:125",
                source_coordinate=initial.source_coordinate,
                reviewer="https://github.com/reviewer",
                reviewed_at="2026-08-24T12:02:00Z",
                review_url=(
                    "https://github.com/ORINOCO-Lite/test-orinoco-downstream-website/"
                    "pull/1#issuecomment-125"
                ),
            )

            snapshot_path = trusted / "site-specific/sources/zotero/content/snapshot.json"
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            source = snapshot.get("source")
            self.assertIsInstance(source, dict)
            source["fetched_at"] = "2026-08-24T12:02:30+00:00"
            snapshot_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            transported = build(root, "transport-changed", trusted_root=trusted)

            self.assertEqual(
                dict(initial.source_coordinate), dict(transported.source_coordinate)
            )
            self.assertEqual(len(initial.candidates), len(transported.candidates))
            for candidate, updated in zip(
                initial.candidates, transported.candidates, strict=True
            ):
                self.assertEqual(candidate.source_namespace, updated.source_namespace)
                self.assertEqual(candidate.source_record_id, updated.source_record_id)
                self.assertEqual(candidate.pid, updated.pid)
                self.assertEqual(candidate.record_path, updated.record_path)
                self.assertEqual(candidate.source_claim, updated.source_claim)
                self.assertEqual(candidate.claim_sha256, updated.claim_sha256)

            cache_path = root / "site-specific/curation-records/zotero.yaml"
            cache_path.write_bytes(serialize_decision_cache(cache))
            filtered = build(root, "transport-filtered", trusted_root=trusted)
            self.assertEqual(filtered.candidates, ())
            self.assertEqual(filtered.file_changes(), ())

    def test_source_presence_adds_but_source_absence_does_not_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepared_root(Path(directory))
            restored = self.plan.candidates[0]
            (root / "site-specific/metadata/records" / restored.record_path).unlink()
            human_only_pid = "xyzrins:publications/human-only"
            human_only = root / "site-specific/metadata/records/XYZPublication/human-only.yaml"
            human_only.write_bytes(
                canonical_yaml_bytes(
                    {
                        "display_label": "Human-only publication",
                        "kind": "bibo:AcademicArticle",
                        "pid": human_only_pid,
                        "schema_type": "xyzri:XYZPublication",
                        "title": "Human-only publication",
                    }
                )
            )

            plan = build(root)
            additions = [
                candidate
                for candidate in plan.candidates
                if candidate.operation.value == "add"
            ]
            self.assertEqual([candidate.pid for candidate in additions], [restored.pid])
            self.assertNotIn(
                human_only_pid,
                {candidate.pid for candidate in plan.candidates},
            )
            self.assertTrue(
                all(
                    candidate.operation.value != "delete"
                    for candidate in plan.candidates
                )
            )

    def test_unreviewed_external_provenance_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            provider.ZoteroCandidateError,
            "must identify a reviewed xyzri:XYZInstrument record",
        ):
            build(
                self.root,
                "missing-agent",
                adapter_agent_pid="https://example.invalid/agents/missing-v1",
            )

    def test_provenance_identity_must_name_a_reviewed_instrument(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepared_root(Path(directory))
            identity_path = (
                root
                / "site-specific/metadata/records/XYZInstrument/source-adapter-zotero-v1.yaml"
            )
            identity = yaml.safe_load(identity_path.read_text(encoding="utf-8"))
            self.assertIsInstance(identity, dict)
            identity["schema_type"] = "xyzri:XYZProject"
            identity_path.write_bytes(canonical_yaml_bytes(identity))

            with self.assertRaisesRegex(
                provider.ZoteroCandidateError,
                "must identify a reviewed xyzri:XYZInstrument record",
            ):
                build(root, "wrong-identity-type")

    def test_noncanonical_candidate_baseline_is_loaded_semantically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = prepared_root(Path(directory))
            candidate = self.plan.candidates[0]
            path = root / "site-specific/metadata/records" / candidate.record_path
            semantic_record = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIsInstance(semantic_record, dict)
            path.write_bytes(b"---\n" + path.read_bytes())
            self.assertNotEqual(
                path.read_bytes(),
                canonical_yaml_bytes(semantic_record),
            )

            plan = build(root, "noncanonical-baseline")

            self.assertEqual(plan, self.plan)
            matching = [item for item in plan.candidates if item.pid == candidate.pid]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0].baseline_record, semantic_record)
            self.assertEqual(
                matching[0].canonical_record_bytes(proposed=True),
                canonical_yaml_bytes(dict(matching[0].proposed_record)),
            )


class ZoteroQualifiedUpdateTests(unittest.TestCase):
    def source_record(self, *, title: str = "Source title") -> dict[str, object]:
        return {
            "display_label": "Source label",
            "kind": "bibo:AcademicArticle",
            "pid": "xyzrins:publications/test",
            "schema_type": "xyzri:XYZPublication",
            "title": title,
        }

    def qualified_baseline(self, *, include_title: bool) -> dict[str, object]:
        record: dict[str, object] = {
            "attributes": [
                {
                    "predicate": "dlthings:title",
                    "schema_type": "dlthings:AttributeSpecification",
                    "value": "Source title",
                },
                {
                    "predicate": "skos:prefLabel",
                    "schema_type": "dlthings:AttributeSpecification",
                    "value": "Source label",
                },
            ],
            "characterized_by": [
                {
                    "object": "bibo:AcademicArticle",
                    "predicate": "dcterms:type",
                }
            ],
            "display_label": "Source label",
            "kind": "bibo:AcademicArticle",
            "pid": "xyzrins:publications/test",
            "schema_type": "xyzri:XYZPublication",
        }
        if include_title:
            record["title"] = "Source title"
        return record

    def candidate(
        self,
        source: dict[str, object],
        *,
        imported_from: str,
    ) -> Candidate:
        proposed, companion = provider.propose_record(
            source,
            None,
            None,
            adapter_agent_pid=AGENT_PID,
            imported_from=imported_from,
            schema=SCHEMA,
        )
        claim = provider.semantic_source_claim(
            source,
            adapter_agent_pid=AGENT_PID,
            imported_from=imported_from,
            schema=SCHEMA,
        )
        return Candidate(
            source_namespace="zotero:group:6197458",
            source_record_id="item:TEST0001",
            pid=str(source["pid"]),
            record_path="XYZPublication/test.yaml",
            baseline_record=None,
            proposed_record=proposed,
            baseline_companion=None,
            proposed_companion=companion,
            source_claim=claim,
        )

    def test_missing_topical_copies_unowned_assertion_without_pav(self) -> None:
        baseline = self.qualified_baseline(include_title=False)
        proposed, companion = provider.propose_record(
            self.source_record(),
            baseline,
            None,
            adapter_agent_pid=AGENT_PID,
            imported_from="https://example.invalid/source/1",
            schema=SCHEMA,
        )

        self.assertEqual(proposed, baseline | {"title": "Source title"})
        self.assertIsNone(companion)

    def test_new_source_objects_are_qualified_and_pav_is_overlay_only(self) -> None:
        source = self.source_record()
        source.update(
            {
                "about": ["https://example.invalid/topics/open-science"],
                "attributes": [
                    {
                        "predicate": "dcterms:issued",
                        "schema_type": "dlthings:AttributeSpecification",
                        "value": "2026",
                    }
                ],
                "attributed_to": [
                    {
                        "object": "https://example.invalid/people/curator",
                        "roles": ["marcrel:aut"],
                    }
                ],
                "identifiers": [
                    {
                        "notation": "zotero:group:6197458:item:TEST0001",
                        "schema_type": "dlthings:Identifier",
                    }
                ],
            }
        )
        source_uri = "https://example.invalid/source/1"
        proposed, companion = provider.propose_record(
            source,
            None,
            None,
            adapter_agent_pid=AGENT_PID,
            imported_from=source_uri,
            schema=SCHEMA,
        )

        assert companion is not None
        self.assertEqual(
            {assertion["path"] for assertion in companion["assertions"]},
            {
                "/attributes",
                "/attributed_to",
                "/characterized_by",
                "/identifiers",
            },
        )
        self.assertEqual(len(companion["assertions"]), 7)
        self.assertTrue(
            all(
                assertion["pav:importedBy"] == AGENT_PID
                and assertion["pav:importedFrom"] == source_uri
                for assertion in companion["assertions"]
            )
        )
        self.assertEqual(proposed["title"], source["title"])
        self.assertEqual(proposed["display_label"], source["display_label"])
        self.assertEqual(proposed["kind"], source["kind"])
        self.assertEqual(proposed["about"], source["about"])
        self.assertEqual(proposed["identifiers"], source["identifiers"])
        self.assertEqual(proposed["attributed_to"], source["attributed_to"])
        self.assertTrue(
            all(
                assertion.get("schema_type") == "dlthings:AttributeSpecification"
                for assertion in proposed["attributes"]
            )
        )
        assert_no_machine_pav(self, proposed)

    def test_claim_hash_tracks_semantics_not_pav_or_adapter_version(self) -> None:
        base = self.source_record()
        base.update(
            {
                "attributes": [
                    {
                        "predicate": "dcterms:issued",
                        "schema_type": "dlthings:AttributeSpecification",
                        "value": "2026",
                    }
                ],
                "generated_by": [
                    {"object": "https://example.invalid/activities/reviewed-policy"}
                ],
            }
        )
        first = self.candidate(
            base,
            imported_from="https://example.invalid/source/1",
        )
        pav_only = self.candidate(
            base,
            imported_from="https://example.invalid/source/2",
        )
        self.assertNotEqual(first.proposed_companion, pav_only.proposed_companion)
        self.assertEqual(first.source_claim, pav_only.source_claim)
        self.assertEqual(first.claim_sha256, pav_only.claim_sha256)
        assert_no_machine_pav(self, dict(first.source_claim))
        self.assertIn(
            {
                "predicate": "dcterms:issued",
                "schema_type": "dlthings:AttributeSpecification",
                "value": "2026",
            },
            first.source_claim["attributes"],
        )
        self.assertEqual(first.source_claim["generated_by"], base["generated_by"])
        self.assertEqual(first.source_claim["schema_type"], base["schema_type"])

        predicate_change = deepcopy(base)
        predicate_change["attributes"][0]["predicate"] = "dcterms:language"
        policy_change = deepcopy(base)
        policy_change["generated_by"][0]["object"] = (
            "https://example.invalid/activities/revised-policy"
        )
        schema_change = deepcopy(base)
        schema_change["schema_type"] = "xyzri:XYZDocument"
        self.assertNotEqual(
            first.claim_sha256,
            self.candidate(
                predicate_change,
                imported_from="https://example.invalid/source/1",
            ).claim_sha256,
        )
        self.assertNotEqual(
            first.claim_sha256,
            self.candidate(
                policy_change,
                imported_from="https://example.invalid/source/1",
            ).claim_sha256,
        )
        self.assertNotEqual(
            first.claim_sha256,
            self.candidate(
                schema_change,
                imported_from="https://example.invalid/source/1",
            ).claim_sha256,
        )

        plans = [
            CandidatePlan(
                adapter="zotero",
                adapter_version=version,
                adapter_agent_pid=AGENT_PID,
                source_namespace="zotero:group:6197458",
                source_coordinate={"library_version": 451},
                metadata_base=METADATA_BASE,
                candidates=(first,),
            )
            for version in ("1", "2")
        ]
        self.assertEqual(
            plans[0].candidates[0].claim_sha256,
            plans[1].candidates[0].claim_sha256,
        )

    def test_populated_topical_survives_differing_source_assertion(self) -> None:
        baseline = self.qualified_baseline(include_title=True)
        baseline["title"] = "Human title"
        baseline["attributes"][0]["value"] = "Human title"
        proposed, companion = provider.propose_record(
            self.source_record(),
            baseline,
            None,
            adapter_agent_pid=AGENT_PID,
            imported_from="https://example.invalid/source/1",
            schema=SCHEMA,
        )

        self.assertEqual(proposed["title"], "Human title")
        source_assertion = {
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Source title",
        }
        self.assertIn(source_assertion, proposed["attributes"])
        self.assertEqual(
            companion,
            {
                "assertions": [
                    {
                        "assertion_sha256": assertion_sha256(source_assertion),
                        "path": "/attributes",
                        "pav:importedBy": AGENT_PID,
                        "pav:importedFrom": "https://example.invalid/source/1",
                    }
                ],
                "record": "xyzrins:publications/test",
            },
        )
        assert_no_machine_pav(self, proposed)

    def test_equivalent_unowned_assertions_do_not_gain_provenance(self) -> None:
        baseline = self.qualified_baseline(include_title=True)
        proposed, companion = provider.propose_record(
            self.source_record(),
            deepcopy(baseline),
            None,
            adapter_agent_pid=AGENT_PID,
            imported_from="https://example.invalid/source/1",
            schema=SCHEMA,
        )

        self.assertEqual(proposed, baseline)
        self.assertIsNone(companion)

    def test_absent_optional_topical_does_not_create_empty_slot(self) -> None:
        machine_description = {
            "predicate": "dcterms:description",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Old machine description",
        }
        baseline = self.qualified_baseline(include_title=True)
        baseline["attributes"].append(machine_description)
        companion = annotation_companion(
            str(baseline["pid"]),
            [pav_entry("/attributes", machine_description)],
        )

        proposed, proposed_companion = provider.propose_record(
            self.source_record(),
            baseline,
            companion,
            adapter_agent_pid=AGENT_PID,
            imported_from="https://example.invalid/source/current",
            schema=SCHEMA,
        )

        self.assertNotIn("description", proposed)
        self.assertEqual(baseline["attributes"][:2], proposed["attributes"])
        self.assertIsNone(proposed_companion)

    def test_source_omission_removes_only_same_owner_assertions(self) -> None:
        same_description = {
            "predicate": "dcterms:description",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Old machine description",
        }
        human_description = {
            "predicate": "dcterms:description",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Human description assertion",
        }
        other_description = {
            "predicate": "dcterms:description",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Other adapter description",
        }
        same_issued = {
            "predicate": "dcterms:issued",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "2025",
        }
        human_issued = {
            "predicate": "dcterms:issued",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "2024",
        }
        other_issued = {
            "predicate": "dcterms:issued",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "2023",
        }
        same_about = {
            "object": "https://example.invalid/topics/old-machine",
            "predicate": "dcterms:subject",
        }
        human_about = {
            "object": "https://example.invalid/topics/human",
            "predicate": "dcterms:subject",
        }
        other_about = {
            "object": "https://example.invalid/topics/other-adapter",
            "predicate": "dcterms:subject",
        }
        same_attribution = {
            "object": "https://example.invalid/people/old-machine",
            "schema_type": "dlthings:Attribution",
        }
        human_attribution = {
            "object": "https://example.invalid/people/human",
            "schema_type": "dlthings:Attribution",
        }
        other_attribution = {
            "object": "https://example.invalid/people/other-adapter",
            "schema_type": "dlthings:Attribution",
        }
        other_agent = "https://example.invalid/agents/other-adapter-v1"
        baseline = self.qualified_baseline(include_title=True)
        baseline["description"] = "Curated top-level description"
        baseline["about"] = ["https://example.invalid/topics/curated-topical"]
        baseline["attributes"].extend(
            [
                same_description,
                human_description,
                other_description,
                same_issued,
                human_issued,
                other_issued,
            ]
        )
        baseline["characterized_by"].extend([same_about, human_about, other_about])
        baseline["attributed_to"] = [
            same_attribution,
            human_attribution,
            other_attribution,
        ]
        companion = annotation_companion(
            str(baseline["pid"]),
            [
                pav_entry("/attributes", same_description),
                pav_entry(
                    "/attributes",
                    other_description,
                    imported_by=other_agent,
                ),
                pav_entry("/attributes", same_issued),
                pav_entry(
                    "/attributes",
                    other_issued,
                    imported_by=other_agent,
                ),
                pav_entry("/characterized_by", same_about),
                pav_entry(
                    "/characterized_by",
                    other_about,
                    imported_by=other_agent,
                ),
                pav_entry("/attributed_to", same_attribution),
                pav_entry(
                    "/attributed_to",
                    other_attribution,
                    imported_by=other_agent,
                ),
            ],
        )

        proposed, proposed_companion = provider.propose_record(
            self.source_record(),
            baseline,
            companion,
            adapter_agent_pid=AGENT_PID,
            imported_from="https://example.invalid/source/current",
            schema=SCHEMA,
        )

        self.assertEqual(
            "Curated top-level description",
            proposed["description"],
        )
        self.assertEqual(
            ["https://example.invalid/topics/curated-topical"],
            proposed["about"],
        )
        self.assertEqual(
            baseline["attributes"][:2]
            + [
                human_description,
                other_description,
                human_issued,
                other_issued,
            ],
            proposed["attributes"],
        )
        self.assertEqual(
            baseline["characterized_by"][:1] + [human_about, other_about],
            proposed["characterized_by"],
        )
        self.assertEqual(
            [human_attribution, other_attribution],
            proposed["attributed_to"],
        )
        assert proposed_companion is not None
        self.assertEqual(4, len(proposed_companion["assertions"]))
        self.assertEqual(
            {other_agent},
            {entry["pav:importedBy"] for entry in proposed_companion["assertions"]},
        )

    def test_source_identity_survives_doi_revision(self) -> None:
        source = {
            "identifiers": [
                {
                    "notation": "zotero:group:6197458:item:STABLE01",
                    "schema_type": "dlthings:Identifier",
                },
                {
                    "notation": "10.0000/original",
                    "schema_type": "dlthings:DOI",
                },
            ],
            "pid": "https://doi.org/10.0000/original",
        }
        revised = deepcopy(source)
        revised["pid"] = "https://doi.org/10.0000/corrected"
        revised["identifiers"][1]["notation"] = "10.0000/corrected"

        source_id, locator = provider.source_identity(source, 6197458)
        revised_id, revised_locator = provider.source_identity(revised, 6197458)
        self.assertEqual(source_id, "item:STABLE01")
        self.assertEqual(revised_id, source_id)
        self.assertEqual(revised_locator, locator)

        duplicate = deepcopy(source)
        duplicate["identifiers"].insert(
            0,
            {
                "notation": "zotero:group:6197458:item:ANOTHER2",
                "schema_type": "dlthings:Identifier",
            },
        )
        self.assertEqual(
            provider.source_identity(duplicate, 6197458),
            (
                "items:ANOTHER2,STABLE01",
                "https://api.zotero.org/groups/6197458/items?"
                "itemKey=ANOTHER2%2CSTABLE01",
            ),
        )


if __name__ == "__main__":
    unittest.main()
