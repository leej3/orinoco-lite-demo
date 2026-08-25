from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from orinoco_lite.annotations import annotation_companion, assertion_sha256
from orinoco_lite.canonical import canonical_yaml_bytes
import yaml

from adapter_fixture import neutralize_reviewed_adapter_state


class AdapterFixtureTests(unittest.TestCase):
    def test_only_exact_adapter_owned_assertions_and_cache_are_removed(self) -> None:
        owned_agent = "xyzrins:source-adapters/zotero/v1"
        other_agent = "https://example.invalid/agents/other-adapter"
        human_assertion = {
            "predicate": "dlthings:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Curated title",
        }
        owned_attribute = {
            "predicate": "skos:prefLabel",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Source label",
        }
        owned_relation = {
            "object": "bibo:AcademicArticle",
            "predicate": "dcterms:type",
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record_path = root / "metadata/records/Thing/example.yaml"
            companion_path = (
                root / "metadata/overlays/annotations/Thing/example.yaml"
            )
            cache = Path("source-adapters/zotero/policy/curation-decisions.yaml")
            record_path.parent.mkdir(parents=True)
            companion_path.parent.mkdir(parents=True)
            (root / cache).parent.mkdir(parents=True)
            record_path.write_bytes(
                canonical_yaml_bytes(
                    {
                        "attributes": [human_assertion, owned_attribute],
                        "characterized_by": [owned_relation],
                        "pid": "https://example.invalid/things/example",
                        "title": "Curated title",
                    }
                )
            )
            companion_path.write_bytes(
                canonical_yaml_bytes(
                    annotation_companion(
                        "https://example.invalid/things/example",
                        [
                            {
                                "assertion_sha256": assertion_sha256(
                                    owned_attribute
                                ),
                                "path": "/attributes",
                                "pav:importedBy": owned_agent,
                                "pav:importedFrom": (
                                    "https://example.invalid/source/one"
                                ),
                            },
                            {
                                "assertion_sha256": assertion_sha256(
                                    owned_relation
                                ),
                                "path": "/characterized_by",
                                "pav:importedBy": owned_agent,
                                "pav:importedFrom": (
                                    "https://example.invalid/source/one"
                                ),
                            },
                            {
                                "assertion_sha256": assertion_sha256(
                                    human_assertion
                                ),
                                "path": "/attributes",
                                "pav:importedBy": other_agent,
                                "pav:importedFrom": (
                                    "https://example.invalid/source/other"
                                ),
                            },
                        ],
                    )
                )
            )
            (root / cache).write_text("adapter: zotero\n", encoding="utf-8")

            removed = neutralize_reviewed_adapter_state(
                root,
                adapter_agent_pids=(owned_agent,),
                decision_caches=(cache,),
            )

            self.assertEqual(2, removed)
            self.assertFalse((root / cache).exists())
            record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
            self.assertEqual("Curated title", record["title"])
            self.assertEqual([human_assertion], record["attributes"])
            self.assertNotIn("characterized_by", record)
            companion = yaml.safe_load(
                companion_path.read_text(encoding="utf-8")
            )
            self.assertEqual(1, len(companion["assertions"]))
            self.assertEqual(
                other_agent,
                companion["assertions"][0]["pav:importedBy"],
            )


if __name__ == "__main__":
    unittest.main()
