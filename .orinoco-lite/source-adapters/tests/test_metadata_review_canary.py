from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
from types import ModuleType
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


review = load_module(
    "orinoco_metadata_review",
    ROOT / ".orinoco-lite/source-adapters/metadata/tools/review.py",
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


class MetadataReviewCanaryTests(unittest.TestCase):
    def test_runner_reads_manifests_in_stable_directory_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            for source_id in ("zeta", "alpha"):
                manifest = source_root / source_id / "source.yaml"
                manifest.parent.mkdir()
                manifest.write_text(
                    "contract_version: 1\n"
                    f"id: {source_id}\n"
                    f"adapter: extensions/source-adapters/{source_id}.py\n"
                    f"provenance_identity: example:{source_id}/v1\n",
                    encoding="utf-8",
                )

            self.assertEqual(
                ["alpha", "zeta"],
                [source["id"] for source in review.load_config(source_root)],
            )

    def test_review_is_read_only_and_refresh_is_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = root / "extensions/source-adapters/example.py"
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
                        staged.write_text('{"version": 2}\\n', encoding="utf-8")
                        empty = {empty}
                        return {
                            "adapter_api_version": 1,
                            "source_id": "example",
                            "canonical_promotion": False,
                            "source": {"reviewed_version": 1, "live_version": 2},
                            "source_diff": empty,
                            "candidate_diff": empty,
                            "canonical_diff": empty,
                            "artifacts": {},
                            "evidence_updates": [{
                                "operation": "replace",
                                "staged": staged.relative_to(root).as_posix(),
                                "destination": (
                                    "site-specific/sources/example/content/"
                                    "snapshot.json"
                                ),
                            }],
                        }
                    """
                ).replace("{empty}", repr(EMPTY_DIFF)),
                encoding="utf-8",
            )
            manifest = root / "site-specific/sources/example/source.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                "contract_version: 1\n"
                "id: example\n"
                "adapter: extensions/source-adapters/example.py\n"
                "provenance_identity: example:source-adapter/v1\n",
                encoding="utf-8",
            )
            build = root / "build/metadata-review"
            destination = root / "site-specific/sources/example/content/snapshot.json"

            report = review.run("review", root=root, config=manifest, build=build)
            self.assertFalse(report["canonical_promotion"])
            self.assertFalse(destination.exists())
            self.assertFalse((root / "site-specific/metadata").exists())

            review.run("refresh-evidence", root=root, config=manifest, build=build)
            self.assertEqual({"version": 2}, json.loads(destination.read_text()))
            self.assertFalse((root / "site-specific/metadata").exists())

    def test_canonical_metadata_is_not_an_evidence_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(review.MetadataReviewError, "outside"):
                review.allowed_destination(
                    Path(temporary),
                    "example",
                    "site-specific/metadata/records/XYZPerson/example.yaml",
                )

    def test_curation_binds_candidate_paths_to_the_downstream_config(self) -> None:
        curation = load_module(
            "orinoco_metadata_curation_canary",
            ROOT / ".orinoco-lite/source-adapters/metadata/tools/curation.py",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            records = root / "site-specific/metadata/records"
            metadata_base = "a" * 40
            adapter_agent_pid = "example:source-adapter/zotero/v1"
            captured: dict[str, str] = {}

            class Workspace:
                def __init__(self) -> None:
                    self.root = root

                def path(self, name: str) -> Path:
                    self.assert_records(name)
                    return records

                @staticmethod
                def assert_records(name: str) -> None:
                    if name != "records":
                        raise AssertionError(name)

            class Plan:
                adapter = "zotero"
                candidates = ()

                def __init__(self) -> None:
                    self.metadata_base = metadata_base
                    self.adapter_agent_pid = adapter_agent_pid

            class Provider:
                @staticmethod
                def build_candidate_plan(*_args, **_kwargs):
                    captured.update(
                        root=os.environ["ORINOCO_ROOT"],
                        records=os.environ["ORINOCO_RECORDS_ROOT"],
                    )
                    return Plan()

            previous = {
                "ORINOCO_ROOT": "/previous/root",
                "ORINOCO_RECORDS_ROOT": "/previous/records",
            }
            with (
                mock.patch.object(
                    curation,
                    "load_workspace",
                    return_value=Workspace(),
                ),
                mock.patch.object(curation, "_head", return_value=metadata_base),
                mock.patch.object(
                    curation,
                    "_load_provider",
                    return_value=Provider,
                ),
                mock.patch.object(curation, "_schema", return_value=object()),
                mock.patch.object(curation, "CandidatePlan", Plan),
                mock.patch.dict(os.environ, previous, clear=False),
            ):
                result = curation.build_plan(
                    root,
                    root / "trusted",
                    adapter="zotero",
                    metadata_base=metadata_base,
                    adapter_agent_pid=adapter_agent_pid,
                    runtime_root=root / "runtime",
                    scratch=root / "build/curation",
                    expected_library_version=1,
                )
                self.assertIsInstance(result, Plan)
                self.assertEqual(os.fspath(root), captured["root"])
                self.assertEqual(os.fspath(records), captured["records"])
                self.assertEqual(
                    previous["ORINOCO_ROOT"],
                    os.environ["ORINOCO_ROOT"],
                )
                self.assertEqual(
                    previous["ORINOCO_RECORDS_ROOT"],
                    os.environ["ORINOCO_RECORDS_ROOT"],
                )


if __name__ == "__main__":
    unittest.main()
