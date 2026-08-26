from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import textwrap
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[3]


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


review = load_module(
    "orinoco_metadata_review", ROOT / "source-adapters/metadata/tools/review.py"
)
zotero = load_module(
    "orinoco_zotero_metadata_adapter",
    ROOT / "source-adapters/zotero/metadata_adapter.py",
)


EMPTY_DIFF = {
    "summary": {
        "added": 0,
        "removed": 0,
        "changed": 0,
        "unchanged": 0,
        "different": False,
    },
    "added": [],
    "removed": [],
    "changed": [],
}


class MetadataReviewHostTests(unittest.TestCase):
    def test_provenance_identity_uses_the_validated_manifest_contract(self) -> None:
        self.assertEqual(
            "xyzrins:source-adapters/zotero/v1",
            review.resolve_provenance_identity("zotero"),
        )
        self.assertEqual(
            "xyzrins:source-adapters/dump-research-info/v2",
            review.resolve_provenance_identity("dump-research-info"),
        )

        cases = (
            (
                "contract_version = 2\n",
                "contract_version is unsupported",
            ),
            (
                "contract_version = 1\n"
                "[[sources]]\n"
                'id = "fake"\n'
                'adapter = "fake.py"\n',
                "has no reviewed provenance_identity",
            ),
            (
                "contract_version = 1\n"
                "[[sources]]\n"
                'id = "fake"\n'
                'adapter = "fake.py"\n'
                'provenance_identity = " first "\n',
                "must be one nonempty line",
            ),
            (
                "contract_version = 1\n"
                "[[sources]]\n"
                'id = "other"\n'
                'adapter = "other.py"\n'
                'provenance_identity = "example:other/v1"\n',
                "Unknown metadata source",
            ),
        )
        for manifest, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as name:
                path = Path(name) / "sources.toml"
                path.write_text(manifest, encoding="utf-8")
                with self.assertRaisesRegex(review.MetadataReviewError, message):
                    review.resolve_provenance_identity("fake", path)

    def test_semantic_diff_reports_identity_and_field_changes(self) -> None:
        before = {
            "gone": {"pid": "gone"},
            "same": {"pid": "same", "value": 1},
            "edit": {"pid": "edit", "value": {"old": True}},
        }
        after = {
            "new": {"pid": "new"},
            "same": {"pid": "same", "value": 1},
            "edit": {"pid": "edit", "value": {"new": True}},
        }

        difference = review.semantic_diff(before, after)

        self.assertEqual(
            difference["summary"],
            {
                "added": 1,
                "removed": 1,
                "changed": 1,
                "unchanged": 1,
                "different": True,
            },
        )
        self.assertEqual(difference["added"][0]["id"], "new")
        self.assertEqual(difference["removed"][0]["id"], "gone")
        self.assertEqual(
            [change["path"] for change in difference["changed"][0]["changes"]],
            ["/value/new", "/value/old"],
        )

    def test_review_is_read_only_and_refresh_replaces_only_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = root / "source-adapters" / "fake" / "adapter.py"
            adapter.parent.mkdir(parents=True)
            adapter.write_text(
                textwrap.dedent(
                    """
                    from pathlib import Path
                    ADAPTER_API_VERSION = 1
                    def review(context):
                        root = Path(context["root"])
                        output = Path(context["output"])
                        staged = output / "snapshot.json"
                        staged.write_text('{"version": 2}\\n')
                        return {
                            "adapter_api_version": 1,
                            "source_id": "fake",
                            "canonical_promotion": False,
                            "source": {"reviewed_version": 1, "live_version": 2},
                            "source_diff": {empty},
                            "candidate_diff": {empty},
                            "canonical_diff": {empty},
                            "artifacts": {},
                            "evidence_updates": [{
                                "operation": "replace",
                                "staged": staged.relative_to(root).as_posix(),
                                "destination": "source-adapters/fake/source/snapshot.json",
                            }],
                        }
                    """
                ).replace("{empty}", repr(EMPTY_DIFF)),
                encoding="utf-8",
            )
            config = root / "sources.toml"
            config.write_text(
                'contract_version = 1\n[[sources]]\nid = "fake"\n'
                'adapter = "source-adapters/fake/adapter.py"\n',
                encoding="utf-8",
            )
            build = root / "build" / "metadata-review"
            destination = root / "source-adapters/fake/source/snapshot.json"

            report = review.run("review", root=root, config=config, build=build)
            self.assertFalse(destination.exists())
            self.assertFalse(report["canonical_promotion"])

            review.run("refresh-evidence", root=root, config=config, build=build)
            self.assertEqual(json.loads(destination.read_text()), {"version": 2})
            self.assertFalse((root / ".orinoco-lite/provenance").exists())

    def test_evidence_destination_cannot_target_canonical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(review.MetadataReviewError, "outside"):
                review.allowed_destination(
                    root, "fake", "metadata/records/XYZPerson/person.yaml"
                )

    def test_refresh_is_blocked_before_mutation_when_review_has_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = root / "source-adapters/fake/adapter.py"
            adapter.parent.mkdir(parents=True)
            adapter.write_text(
                textwrap.dedent(
                    """
                    from pathlib import Path
                    ADAPTER_API_VERSION = 1
                    def review(context):
                        root = Path(context["root"])
                        output = Path(context["output"])
                        staged = output / "snapshot.json"
                        staged.write_text('{"version": 2}\\n')
                        return {
                            "adapter_api_version": 1,
                            "source_id": "fake",
                            "canonical_promotion": False,
                            "source": {},
                            "source_diff": {empty},
                            "candidate_diff": {empty},
                            "canonical_diff": {empty},
                            "blockers": ["policy changed"],
                            "artifacts": {},
                            "evidence_updates": [{
                                "operation": "replace",
                                "staged": staged.relative_to(root).as_posix(),
                                "destination": "source-adapters/fake/source/snapshot.json",
                            }],
                        }
                    """
                ).replace("{empty}", repr(EMPTY_DIFF)),
                encoding="utf-8",
            )
            config = root / "sources.toml"
            config.write_text(
                'contract_version = 1\n[[sources]]\nid = "fake"\n'
                'adapter = "source-adapters/fake/adapter.py"\n',
                encoding="utf-8",
            )
            destination = root / "source-adapters/fake/source/snapshot.json"

            with self.assertRaisesRegex(review.MetadataReviewError, "policy changed"):
                review.run(
                    "refresh-evidence",
                    root=root,
                    config=config,
                    build=root / "build/metadata-review",
                )

            self.assertFalse(destination.exists())

    def test_optional_source_runs_only_when_selected_and_receives_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = root / "source-adapters/optional/adapter.py"
            adapter.parent.mkdir(parents=True)
            adapter.write_text(
                textwrap.dedent(
                    """
                    ADAPTER_API_VERSION = 1
                    def review(context):
                        supplied = context["source_input"]
                        empty = {
                            "summary": {"added": 0, "removed": 0, "changed": 0,
                                        "unchanged": 0, "different": False},
                            "added": [], "removed": [], "changed": [],
                        }
                        return {
                            "adapter_api_version": 1,
                            "source_id": "optional",
                            "canonical_promotion": False,
                            "source": {"input": supplied},
                            "source_diff": empty,
                            "candidate_diff": empty,
                            "canonical_diff": empty,
                            "artifacts": {},
                            "evidence_updates": [],
                        }
                    """
                ),
                encoding="utf-8",
            )
            config = root / "sources.toml"
            config.write_text(
                "contract_version = 1\n[[sources]]\nid = \"optional\"\n"
                "adapter = \"source-adapters/optional/adapter.py\"\n"
                "enabled_by_default = false\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(review.MetadataReviewError, "No metadata"):
                review.run(
                    "review",
                    root=root,
                    config=config,
                    build=root / "build/default",
                )

            result = review.run(
                "review",
                root=root,
                config=config,
                build=root / "build/selected",
                selected_sources=["optional"],
                source_inputs={"optional": "/pinned/checkout"},
            )
            self.assertEqual(
                result["sources"][0]["source"]["input"], "/pinned/checkout"
            )


class ZoteroAdapterContractTests(unittest.TestCase):
    def test_canonical_projection_allows_ordered_additive_enrichment(self) -> None:
        reviewed = {
            "publication": {
                "pid": "publication",
                "identifiers": [
                    {"notation": "doi:example", "schema_type": "Identifier"},
                    {"notation": "zotero:item", "schema_type": "Identifier"},
                ],
                "title": "Reviewed title",
            }
        }
        canonical = {
            "other-source-publication": {"pid": "other-source-publication"},
            "publication": {
                "pid": "publication",
                "identifiers": [
                    {"notation": "doi:example", "schema_type": "Identifier"},
                    {"notation": "crossref:example", "schema_type": "Identifier"},
                    {"notation": "zotero:item", "schema_type": "Identifier"},
                ],
                "title": "Reviewed title",
                "description": "Added by another reviewed adapter",
            },
        }

        self.assertEqual(
            zotero.canonical_projection_violations(reviewed, canonical), []
        )

    def test_canonical_projection_rejects_missing_changed_or_reordered_data(
        self,
    ) -> None:
        reviewed = {
            "publication": {
                "pid": "publication",
                "identifiers": ["first", "second"],
                "title": "Reviewed title",
            },
            "missing": {"pid": "missing"},
        }
        canonical = {
            "publication": {
                "pid": "publication",
                "identifiers": ["second", "first"],
                "title": "Changed title",
            }
        }

        self.assertEqual(
            zotero.canonical_projection_violations(reviewed, canonical),
            [
                "/publication/identifiers/1",
                "/publication/title",
                "/missing",
            ],
        )

    def test_snapshot_map_namespaces_collection_and_item_keys(self) -> None:
        snapshot = {
            "collections": [{"data": {"key": "SAME", "name": "Articles"}}],
            "items": [{"data": {"key": "SAME", "title": "Article"}}],
        }
        self.assertEqual(
            set(zotero.snapshot_map(snapshot)), {"collection:SAME", "item:SAME"}
        )

    def test_candidate_map_rejects_cross_class_pid_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for class_name in ("XYZDataset", "XYZPublication"):
                (root / f"{class_name}.json").write_text(
                    json.dumps([{"pid": "duplicate"}]), encoding="utf-8"
                )
            with self.assertRaisesRegex(zotero.ZoteroAdapterError, "duplicated"):
                zotero.candidate_map(root)

    def test_canonical_export_skips_only_the_record_root_control_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = root / "metadata/records"
            (records / "XYZPerson").mkdir(parents=True)
            (records / ".dumpthings.yaml").write_text("type: file\n", encoding="utf-8")
            (records / "XYZPerson/person.yaml").write_text(
                "pid: person\nschema_type: dlthings:Person\n", encoding="utf-8"
            )
            destination = root / "build/index"
            index = zotero.export_canonical_json(root, destination)
            self.assertEqual(set(index), {"XYZPerson"})
            self.assertEqual(json.loads(index["XYZPerson"].read_text())[0]["pid"], "person")

    def test_noncanonical_mapping_identities_are_explicit_and_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical_people = root / "canonical/XYZPerson.json"
            canonical_organizations = root / "canonical/XYZOrganization.json"
            canonical_people.parent.mkdir(parents=True)
            canonical_people.write_text(
                json.dumps([{"pid": "xyzrins:persons/published"}]), encoding="utf-8"
            )
            canonical_organizations.write_text("[]", encoding="utf-8")
            identities = root / "review-identities.yaml"
            identities.write_text(
                textwrap.dedent(
                    """
                    format_version: 1
                    identities:
                    - pid: xyzrins:persons/source-only
                      aliases: [Source Only, Source Alias]
                    """
                ),
                encoding="utf-8",
            )

            closure = zotero.export_noncanonical_mapping_identities(
                identities,
                {
                    "XYZPerson": canonical_people,
                    "XYZOrganization": canonical_organizations,
                },
                root / "build/closure",
            )

            self.assertEqual(closure["identities"], ["xyzrins:persons/source-only"])
            self.assertEqual(
                json.loads(closure["people_path"].read_text()),
                [
                    {"display_label": "Source Only", "pid": "xyzrins:persons/source-only"},
                    {"display_label": "Source Alias", "pid": "xyzrins:persons/source-only"},
                ],
            )
            self.assertFalse((root / "metadata/records").exists())


if __name__ == "__main__":
    unittest.main()
