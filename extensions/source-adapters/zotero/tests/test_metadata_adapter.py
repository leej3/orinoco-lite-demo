from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
import sys
import tempfile
import textwrap
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[4]


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


zotero = load_module(
    "orinoco_zotero_metadata_adapter_tests",
    ROOT / "extensions/source-adapters/zotero/metadata_adapter.py",
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
            records = root / "site-specific/metadata/records"
            (records / "XYZPerson").mkdir(parents=True)
            (records / ".dumpthings.yaml").write_text("type: file\n", encoding="utf-8")
            (records / "XYZPerson/person.yaml").write_text(
                "pid: person\nschema_type: dlthings:Person\n", encoding="utf-8"
            )
            shutil.copyfile(ROOT / "orinoco.yaml", root / "orinoco.yaml")
            shutil.copyfile(ROOT / "site-specific/site.yaml", root / "site-specific/site.yaml")
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
            self.assertFalse((root / "site-specific/metadata/records").exists())


if __name__ == "__main__":
    unittest.main()
