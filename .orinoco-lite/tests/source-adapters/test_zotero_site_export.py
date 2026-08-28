from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / ".orinoco-lite/source-adapters/zotero/tools"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "zotero_site_export", SCRIPTS / "zotero_site_export.py"
)
assert SPEC is not None and SPEC.loader is not None
EXPORT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EXPORT
SPEC.loader.exec_module(EXPORT)


class ZoteroSiteExportTests(unittest.TestCase):
    def build_temporary_directory(self) -> tempfile.TemporaryDirectory[str]:
        EXPORT.BUILD_ROOT.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=EXPORT.BUILD_ROOT)

    def export(
        self,
        root: Path,
        *,
        output: Path | None = None,
        policy: Path | None = None,
        report: Path | None = None,
        build_root: Path | None = None,
    ) -> tuple[Path, Path]:
        output = output or root / EXPORT.OUTPUT_RELATIVE_PATH
        report = report or root / EXPORT.REPORT_RELATIVE_PATH
        EXPORT.command_export(
            argparse.Namespace(
                publications=(
                    ROOT / "site-specific/sources/zotero/evidence/candidates/XYZPublication.json"
                ),
                snapshot=(
                    ROOT / "site-specific/sources/zotero/content/snapshot.json"
                ),
                policy=policy
                or ROOT
                / "site-specific/sources/zotero/policy/site-policy.yaml",
                output_dir=output,
                report=report,
            ),
            build_root=build_root or root,
        )
        return output, report

    def test_full_export_is_deterministic_and_uses_explicit_curie_types(self) -> None:
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            first, first_report = self.export(root / "first")
            second, second_report = self.export(root / "second")
            first_files = {
                path.name: path.read_bytes() for path in sorted(first.glob("*.yaml"))
            }
            second_files = {
                path.name: path.read_bytes() for path in sorted(second.glob("*.yaml"))
            }
            self.assertEqual(first_files, second_files)
            self.assertEqual(first_report.read_bytes(), second_report.read_bytes())

            records = [yaml.safe_load(payload) for payload in first_files.values()]
            report = json.loads(first_report.read_text(encoding="utf-8"))

        self.assertEqual(len(records), 126)
        self.assertEqual(report["output"]["publication_count"], 126)
        self.assertIn(
            "xyzrins:publications/datalad-joss-2021",
            {record["pid"] for record in records},
        )
        datalad = next(
            record
            for record in records
            if record["pid"] == "xyzrins:publications/datalad-joss-2021"
        )
        self.assertEqual(
            datalad["generated_by"],
            [
                {
                    "object": "xyzrins:projects/datalad",
                    "at_location": "ISSN:2475-9066",
                    "at_time": "2021-07-01",
                    "schema_type": "dlthings:Generation",
                }
            ],
        )
        self.assertTrue(
            all(record["schema_type"] == "xyzri:XYZPublication" for record in records)
        )
        self.assertEqual(
            [record["pid"] for record in records if "generated_by" in record],
            ["xyzrins:publications/datalad-joss-2021"],
        )
        for record in records:
            for attribution in record.get("attributed_to", []):
                self.assertEqual(attribution["schema_type"], "dlthings:Attribution")
            for attribute in record.get("attributes", []):
                self.assertEqual(
                    attribute["schema_type"],
                    "dlthings:AttributeSpecification",
                )
        canonical = {
            path.name
            for path in (ROOT / "site-specific/metadata/records/XYZPublication").glob("*.yaml")
        }
        self.assertLessEqual(set(first_files), canonical)
        canonical_people = {
            yaml.safe_load(path.read_text(encoding="utf-8"))["pid"]
            for path in (ROOT / "site-specific/metadata/records/XYZPerson").glob("*.yaml")
        }
        self.assertLessEqual(
            {
                "xyzrins:persons/brock-wester",
                "xyzrins:persons/russell-poldrack",
            },
            canonical_people,
        )
        attribution_counts = Counter(
            attribution["object"]
            for record in records
            for attribution in record.get("attributed_to", [])
        )
        self.assertEqual(attribution_counts["xyzrins:persons/brock-wester"], 2)
        self.assertEqual(attribution_counts["xyzrins:persons/russell-poldrack"], 19)
        self.assertEqual(
            report["reviewed_omissions"],
            {"omitted generation obo:IAO_0000444": 38},
        )

    def test_export_hashes_the_same_single_reads_that_it_renders(self) -> None:
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            original_read = EXPORT.read_input_bytes
            reads: dict[Path, int] = {}

            def read_once(path: Path) -> bytes:
                reads[path] = reads.get(path, 0) + 1
                if reads[path] > 1:
                    raise AssertionError(f"input was read more than once: {path}")
                return original_read(path)

            with mock.patch.object(
                EXPORT, "read_input_bytes", side_effect=read_once
            ):
                self.export(root)

            self.assertEqual(len(reads), 3)
            self.assertEqual(set(reads.values()), {1})

    def test_export_fails_closed_on_an_unreviewed_relationship_target(self) -> None:
        policy = EXPORT.load_policy(
            ROOT / "site-specific/sources/zotero/policy/site-policy.yaml"
        )
        record = {
            "pid": "https://doi.org/10.1234/example",
            "title": "Example",
            "kind": "bibo:Article",
            "identifiers": [
                {"notation": "10.1234/example", "schema_type": "dlthings:DOI"},
                {
                    "notation": "zotero:group:6197458:item:ABCD1234",
                    "schema_type": "dlthings:Identifier",
                },
            ],
            "attributed_to": [
                {"object": "xyzrins:persons/unreviewed", "roles": ["marcrel:aut"]}
            ],
        }
        with self.assertRaisesRegex(ValueError, "unreviewed attribution target"):
            EXPORT.render_publication(record, deepcopy(policy), EXPORT.Counter())

    def test_export_rejects_destinations_outside_repository_build_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "strict descendant"):
                self.export(root, build_root=EXPORT.BUILD_ROOT)

        with self.assertRaisesRegex(ValueError, "strict descendant"):
            self.export(
                EXPORT.BUILD_ROOT,
                output=EXPORT.BUILD_ROOT,
                report=EXPORT.BUILD_ROOT / "report.json",
                build_root=EXPORT.BUILD_ROOT,
            )

        with self.build_temporary_directory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "dedicated build artifacts"):
                self.export(root, output=root / "another-build-artifact")

    def test_export_rejects_overlapping_and_symlink_destinations(self) -> None:
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            output = root / EXPORT.OUTPUT_RELATIVE_PATH
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                self.export(root, output=output, report=output / "report.json")

            external = root.parent / f"{root.name}-external"
            external.mkdir()
            try:
                output.symlink_to(external, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink component"):
                    self.export(root, output=output)

                output.unlink()
                output.mkdir()
                sentinel = external / "sentinel.yaml"
                sentinel.write_text("outside\n", encoding="utf-8")
                (output / "linked.yaml").symlink_to(sentinel)
                with self.assertRaisesRegex(ValueError, "symlink descendant"):
                    self.export(root, output=output)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside\n")

                (output / "linked.yaml").unlink()
                report = root / EXPORT.REPORT_RELATIVE_PATH
                report.symlink_to(sentinel)
                with self.assertRaisesRegex(ValueError, "symlink component"):
                    self.export(root, output=output, report=report)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside\n")
            finally:
                for path in external.iterdir():
                    path.unlink()
                external.rmdir()

    def test_export_rejects_an_input_inside_the_output_artifact(self) -> None:
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            output = root / EXPORT.OUTPUT_RELATIVE_PATH
            output.mkdir()
            policy = output / "site-policy.yaml"
            policy.write_bytes(
                (
                    ROOT
                    / "site-specific/sources/zotero/policy/site-policy.yaml"
                ).read_bytes()
            )
            (root / EXPORT.REPORT_RELATIVE_PATH).write_text(
                "existing report\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                self.export(root, output=output, policy=policy)
            self.assertTrue(policy.exists())

    def test_export_preserves_unowned_content_at_the_dedicated_path(self) -> None:
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            output = root / EXPORT.OUTPUT_RELATIVE_PATH
            report = root / EXPORT.REPORT_RELATIVE_PATH
            output.mkdir()
            sentinel = output / "notes.txt"
            sentinel.write_text("human work\n", encoding="utf-8")
            report.write_text("human report\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unowned entries"):
                self.export(root, output=output, report=report)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "human work\n")
            self.assertEqual(report.read_text(encoding="utf-8"), "human report\n")

    def test_publish_failure_restores_the_previous_complete_artifact(self) -> None:
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            output = root / EXPORT.OUTPUT_RELATIVE_PATH
            report = root / EXPORT.REPORT_RELATIVE_PATH
            output.mkdir()
            old_record = output / "old.yaml"
            old_record.write_text("old record\n", encoding="utf-8")
            report.write_text("old report\n", encoding="utf-8")
            original_replace = os.replace
            failed = False

            def fail_report_replace(source: os.PathLike[str], target: os.PathLike[str]):
                nonlocal failed
                if Path(target) == report and not failed:
                    failed = True
                    raise OSError("simulated report publication failure")
                return original_replace(source, target)

            with mock.patch.object(
                EXPORT.os, "replace", side_effect=fail_report_replace
            ):
                with self.assertRaisesRegex(OSError, "simulated"):
                    self.export(root, output=output, report=report)

            self.assertEqual(old_record.read_text(encoding="utf-8"), "old record\n")
            self.assertEqual(report.read_text(encoding="utf-8"), "old report\n")
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                sorted(
                    [
                        str(EXPORT.OUTPUT_RELATIVE_PATH),
                        str(EXPORT.REPORT_RELATIVE_PATH),
                    ]
                ),
            )

    def test_interrupt_at_publish_boundaries_restores_the_prior_artifact(self) -> None:
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            for fail_after in (1, 3, 4):
                with self.subTest(fail_after=fail_after):
                    case_root = root / str(fail_after)
                    output = case_root / EXPORT.OUTPUT_RELATIVE_PATH
                    report = case_root / EXPORT.REPORT_RELATIVE_PATH
                    output.mkdir(parents=True)
                    old_record = output / "old.yaml"
                    old_record.write_text("old record\n", encoding="utf-8")
                    report.write_text("old report\n", encoding="utf-8")
                    original_replace = os.replace
                    call_count = 0

                    def interrupt_after_replace(
                        source: os.PathLike[str], target: os.PathLike[str]
                    ):
                        nonlocal call_count
                        call_count += 1
                        result = original_replace(source, target)
                        if call_count == fail_after:
                            raise KeyboardInterrupt
                        return result

                    with mock.patch.object(
                        EXPORT.os, "replace", side_effect=interrupt_after_replace
                    ):
                        with self.assertRaises(KeyboardInterrupt):
                            self.export(
                                case_root,
                                output=output,
                                report=report,
                            )

                    self.assertEqual(
                        old_record.read_text(encoding="utf-8"), "old record\n"
                    )
                    self.assertEqual(
                        report.read_text(encoding="utf-8"), "old report\n"
                    )
                    self.assertEqual(
                        sorted(path.name for path in case_root.iterdir()),
                        sorted(
                            [
                                str(EXPORT.OUTPUT_RELATIVE_PATH),
                                str(EXPORT.REPORT_RELATIVE_PATH),
                            ]
                        ),
                    )

    def test_successful_publish_replaces_the_complete_prior_artifact(self) -> None:
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            output = root / EXPORT.OUTPUT_RELATIVE_PATH
            report = root / EXPORT.REPORT_RELATIVE_PATH
            output.mkdir()
            (output / "stale.yaml").write_text("stale\n", encoding="utf-8")
            report.write_text("stale report\n", encoding="utf-8")

            self.export(root, output=output, report=report)

            self.assertFalse((output / "stale.yaml").exists())
            self.assertEqual(len(list(output.glob("*.yaml"))), 126)
            self.assertEqual(
                json.loads(report.read_text(encoding="utf-8"))["output"][
                    "publication_count"
                ],
                126,
            )
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                sorted(
                    [
                        str(EXPORT.OUTPUT_RELATIVE_PATH),
                        str(EXPORT.REPORT_RELATIVE_PATH),
                    ]
                ),
            )

    def test_export_rejects_every_unused_policy_category(self) -> None:
        policy_path = (
            ROOT / "site-specific/sources/zotero/policy/site-policy.yaml"
        )
        base_policy = EXPORT.load_policy(policy_path)

        def add_pid_override(policy: dict) -> None:
            policy["pid_overrides"]["https://doi.org/10.0000/unused"] = (
                "xyzrins:publications/unused"
            )

        def add_allowed_attribution(policy: dict) -> None:
            policy["allowed_attribution_targets"].append("xyzrins:persons/unused")

        def add_omitted_attribution(policy: dict) -> None:
            policy["omitted_attribution_targets"]["xyzrins:persons/unused"] = (
                "Test-only stale decision."
            )

        def add_allowed_topic(policy: dict) -> None:
            policy["allowed_about_targets"].append("xyzrins:topics/unused")

        def add_omitted_generation(policy: dict) -> None:
            policy["omitted_generation_objects"]["xyzrins:activities/unused"] = (
                "Test-only stale decision."
            )

        def add_allowed_curated_target(policy: dict) -> None:
            policy["allowed_curated_generation_targets"].append(
                "xyzrins:projects/unused"
            )

        def add_stale_curated_source(policy: dict) -> None:
            policy["curated_generations"]["https://doi.org/10.0000/unused"] = [
                {
                    "object": "xyzrins:projects/datalad",
                    "rationale": "Test-only stale decision.",
                }
            ]

        cases = (
            ("pid_overrides", add_pid_override),
            ("allowed_attribution_targets", add_allowed_attribution),
            ("omitted_attribution_targets", add_omitted_attribution),
            ("allowed_about_targets", add_allowed_topic),
            ("omitted_generation_objects", add_omitted_generation),
            ("allowed_curated_generation_targets", add_allowed_curated_target),
            ("curated_generations", add_stale_curated_source),
        )
        with self.build_temporary_directory() as directory:
            root = Path(directory)
            for index, (label, mutate) in enumerate(cases):
                with self.subTest(label=label):
                    case_root = root / str(index)
                    case_root.mkdir()
                    policy = deepcopy(base_policy)
                    mutate(policy)
                    candidate_policy = case_root / "policy.yaml"
                    candidate_policy.write_text(
                        yaml.safe_dump(policy, allow_unicode=True, sort_keys=False),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        f"Unused site migration policy entries: .*{label}",
                    ):
                        self.export(case_root, policy=candidate_policy)
                    self.assertFalse(
                        (case_root / EXPORT.OUTPUT_RELATIVE_PATH).exists()
                    )
                    self.assertFalse(
                        (case_root / EXPORT.REPORT_RELATIVE_PATH).exists()
                    )

    def test_curated_generation_entry_usage_is_checked_independently(self) -> None:
        policy = EXPORT.load_policy(
            ROOT / "site-specific/sources/zotero/policy/site-policy.yaml"
        )
        usage = {
            field: set(values)
            for field, values in EXPORT.declared_policy_entries(policy).items()
        }
        usage["curated_generation_entries"].pop()
        with self.assertRaisesRegex(ValueError, "curated_generation_entries"):
            EXPORT.require_exact_policy_usage(policy, usage)

    def test_policy_rejects_unknown_duplicate_and_conflicting_entries(self) -> None:
        source = (
            ROOT / "site-specific/sources/zotero/policy/site-policy.yaml"
        ).read_text(encoding="utf-8")
        with self.build_temporary_directory() as directory:
            root = Path(directory)

            unknown = root / "unknown.yaml"
            unknown.write_text(
                source + "unknown_policy_field: true\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "unknown=.*unknown_policy_field"):
                EXPORT.load_policy(unknown)

            duplicate = root / "duplicate.yaml"
            duplicate.write_text(source + "pid_overrides: {}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate YAML mapping key"):
                EXPORT.load_policy(duplicate)

            conflicting = deepcopy(
                EXPORT.load_policy(
                    ROOT
                    / "site-specific/sources/zotero/policy/site-policy.yaml"
                )
            )
            conflicting["omitted_attribution_targets"][
                conflicting["allowed_attribution_targets"][0]
            ] = "Test-only conflict."
            conflict_path = root / "conflict.yaml"
            conflict_path.write_text(
                yaml.safe_dump(conflicting, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "both allowed and omitted"):
                EXPORT.load_policy(conflict_path)

            empty_curated = deepcopy(
                EXPORT.load_policy(
                    ROOT
                    / "site-specific/sources/zotero/policy/site-policy.yaml"
                )
            )
            source_pid = next(iter(empty_curated["curated_generations"]))
            empty_curated["curated_generations"][source_pid] = []
            empty_path = root / "empty-curated.yaml"
            empty_path.write_text(
                yaml.safe_dump(empty_curated, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-empty lists"):
                EXPORT.load_policy(empty_path)

            duplicate_curated = deepcopy(
                EXPORT.load_policy(
                    ROOT
                    / "site-specific/sources/zotero/policy/site-policy.yaml"
                )
            )
            source_pid = next(iter(duplicate_curated["curated_generations"]))
            duplicate = deepcopy(
                duplicate_curated["curated_generations"][source_pid][0]
            )
            duplicate["rationale"] = "A second rationale must not duplicate evidence."
            duplicate_curated["curated_generations"][source_pid].append(duplicate)
            duplicate_path = root / "duplicate-curated.yaml"
            duplicate_path.write_text(
                yaml.safe_dump(duplicate_curated, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate entries"):
                EXPORT.load_policy(duplicate_path)


if __name__ == "__main__":
    unittest.main()
