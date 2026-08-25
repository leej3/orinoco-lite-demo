from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "orinoco_shacl_vue_handoff_test",
    ROOT / ".orinoco-lite/tools/shacl_vue_handoff.py",
)
assert SPEC is not None and SPEC.loader is not None
HANDOFF = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HANDOFF
SPEC.loader.exec_module(HANDOFF)


class Repository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Curator")
        self.git("config", "user.email", "curator@example.test")
        self.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Before\n",
        )
        self.write("README.md", "trusted code and policy\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.base = self.head

    @property
    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", os.fspath(self.root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def write(self, relative: str, value: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.head

    def bundle(self, source_commit: str | None = None) -> dict[str, object]:
        source_commit = source_commit or self.head
        return {
            "format": "orinoco-shacl-review-bundle",
            "records": [
                {
                    "pid": "https://example.test/projects/example",
                    "rdf_turtle": "<https://example.test/projects/example> <x:p> <x:o> .\n",
                    "schema_type": "xyzri:XYZProject",
                    "source_path": "metadata/records/XYZProject/example.yaml",
                    "source_sha256": "0" * 64,
                }
            ],
            "source_commit": source_commit,
            "version": 2,
        }

    def handoff(self, *, source_commit: str | None = None) -> str:
        self.write(
            HANDOFF.HANDOFF_PATH,
            json.dumps(self.bundle(source_commit), sort_keys=True) + "\n",
        )
        return self.commit("temporary SHACL Vue handoff")


class HandoffHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Repository(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def inspect(self, *, base_sha: str | None = None) -> dict[str, object]:
        return HANDOFF.inspect_proposal(
            self.repository.root,
            base_sha=base_sha or self.repository.base,
            head_sha=self.repository.head,
        )

    def test_fixed_bundle_only_head_is_bound_to_its_exact_parent(self) -> None:
        parent = self.repository.head
        head = self.repository.handoff()

        report = self.inspect()

        self.assertEqual("handoff", report["phase"])
        self.assertEqual(parent, report["parent_sha"])
        self.assertEqual(parent, report["source_commit"])
        self.assertEqual(head, report["head_sha"])
        self.assertEqual([HANDOFF.HANDOFF_PATH], report["paths"])
        self.assertEqual(1, report["record_count"])

        output = self.repository.root.parent / "ephemeral-bundle.json"
        extracted = HANDOFF.extract_bundle(
            self.repository.root,
            head_sha=head,
            output=output,
        )
        self.assertEqual(parent, extracted["source_commit"])
        self.assertEqual(
            (self.repository.root / HANDOFF.HANDOFF_PATH).read_bytes(),
            output.read_bytes(),
        )

    def test_existing_curation_history_and_human_metadata_are_preserved(self) -> None:
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Proposal\n",
        )
        proposal = self.repository.commit("source proposal")
        self.repository.write(
            "source-adapters/zotero/policy/curation-decisions.yaml",
            "format: orinoco-lite-curation-decisions-v1\ndecisions: {}\n",
        )
        reviewed = self.repository.commit("review decision")
        head = self.repository.handoff()

        report = self.inspect()

        self.assertEqual("handoff", report["phase"])
        self.assertEqual(reviewed, report["parent_sha"])
        self.assertEqual(3, report["commit_count"])
        self.assertTrue(
            self.repository.git("merge-base", "--is-ancestor", proposal, head)
        )

    def test_repeated_standalone_edit_uses_the_previous_exact_head(self) -> None:
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: First standalone edit\n",
        )
        first_edit = self.repository.commit("first standalone edit")
        head = self.repository.handoff()

        report = self.inspect()

        self.assertEqual("handoff", report["phase"])
        self.assertEqual(first_edit, report["parent_sha"])
        self.assertEqual(first_edit, report["source_commit"])
        self.assertEqual(head, report["head_sha"])
        self.assertEqual(2, report["commit_count"])

    def test_stale_mixed_and_nonregular_handoffs_fail_closed(self) -> None:
        self.repository.handoff(source_commit="f" * 40)
        with self.assertRaisesRegex(
            HANDOFF.HandoffError,
            "source_commit must equal",
        ):
            self.inspect()

        self.repository.git("reset", "--hard", self.repository.base)
        self.repository.write(
            HANDOFF.HANDOFF_PATH, json.dumps(self.repository.bundle())
        )
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Mixed\n",
        )
        self.repository.commit("mixed handoff")
        with self.assertRaisesRegex(HANDOFF.HandoffError, "add exactly"):
            self.inspect()

        self.repository.git("reset", "--hard", self.repository.base)
        path = self.repository.root / HANDOFF.HANDOFF_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("../../README.md")
        self.repository.commit("symlink handoff")
        with self.assertRaisesRegex(HANDOFF.HandoffError, "regular Git blob"):
            self.inspect()

    def test_reverted_code_change_in_prior_history_is_rejected(self) -> None:
        self.repository.write("untrusted.py", "raise SystemExit('do not run')\n")
        self.repository.commit("temporarily add code")
        (self.repository.root / "untrusted.py").unlink()
        self.repository.commit("remove code")
        self.repository.handoff()

        with self.assertRaisesRegex(HANDOFF.HandoffError, "unapproved path"):
            self.inspect()

    def test_canonical_head_may_only_change_approved_metadata(self) -> None:
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Canonical\n",
        )
        head = self.repository.commit("canonical human edit")

        report = self.inspect()

        self.assertEqual("canonical", report["phase"])
        self.assertEqual(self.repository.base, report["parent_sha"])
        self.assertEqual(head, report["head_sha"])

        self.repository.write("README.md", "mixed canonical edit\n")
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Mixed canonical\n",
        )
        self.repository.commit("mixed canonical head")
        with self.assertRaisesRegex(HANDOFF.HandoffError, "also changes"):
            self.inspect()

    def test_canonical_head_may_merge_the_exact_trusted_base(self) -> None:
        self.repository.git("checkout", "-qb", "proposal")
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Canonical proposal\n",
        )
        proposal = self.repository.commit("canonical proposal")
        self.repository.git("checkout", "-q", "main")
        self.repository.write("README.md", "reviewed trusted update\n")
        base = self.repository.commit("trusted default update")
        self.repository.git("checkout", "-q", "proposal")
        self.repository.git("merge", "--no-edit", base)

        report = self.inspect(base_sha=base)

        self.assertEqual("canonical", report["phase"])
        self.assertEqual(base, report["parent_sha"])
        self.assertEqual(2, report["commit_count"])
        self.assertEqual(
            ["metadata/records/XYZProject/example.yaml"], report["paths"]
        )
        self.assertTrue(
            self.repository.git(
                "merge-base", "--is-ancestor", proposal, self.repository.head
            )
        )

    def test_one_parent_handoff_may_follow_a_trusted_base_merge(self) -> None:
        self.repository.git("checkout", "-qb", "proposal")
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Canonical proposal\n",
        )
        self.repository.commit("canonical proposal")
        self.repository.git("checkout", "-q", "main")
        self.repository.write("README.md", "reviewed trusted update\n")
        base = self.repository.commit("trusted default update")
        self.repository.git("checkout", "-q", "proposal")
        self.repository.git("merge", "--no-edit", base)
        source_commit = self.repository.head
        handoff = self.repository.handoff()

        report = self.inspect(base_sha=base)

        self.assertEqual("handoff", report["phase"])
        self.assertEqual(source_commit, report["parent_sha"])
        self.assertEqual(source_commit, report["source_commit"])
        self.assertEqual(handoff, report["head_sha"])
        self.assertEqual(3, report["commit_count"])

    def test_merge_head_cannot_be_the_temporary_handoff(self) -> None:
        self.repository.git("checkout", "-qb", "proposal")
        self.repository.handoff()
        self.repository.git("checkout", "-q", "main")
        self.repository.write("README.md", "reviewed trusted update\n")
        base = self.repository.commit("trusted default update")
        self.repository.git("checkout", "-q", "proposal")
        self.repository.git("merge", "--no-edit", base)

        with self.assertRaisesRegex(HANDOFF.HandoffError, "one-parent commit"):
            self.inspect(base_sha=base)

    def test_merge_history_still_rejects_reverted_untrusted_code(self) -> None:
        self.repository.git("checkout", "-qb", "proposal")
        self.repository.write("untrusted.py", "raise SystemExit('do not run')\n")
        self.repository.commit("temporarily add code")
        (self.repository.root / "untrusted.py").unlink()
        self.repository.commit("remove code")
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Canonical proposal\n",
        )
        self.repository.commit("canonical proposal")
        self.repository.git("checkout", "-q", "main")
        self.repository.write("README.md", "reviewed trusted update\n")
        base = self.repository.commit("trusted default update")
        self.repository.git("checkout", "-q", "proposal")
        self.repository.git("merge", "--no-edit", base)

        with self.assertRaisesRegex(HANDOFF.HandoffError, "unapproved path"):
            self.inspect(base_sha=base)

    def test_merge_resolution_may_not_add_an_unapproved_path(self) -> None:
        self.repository.git("checkout", "-qb", "proposal")
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Canonical proposal\n",
        )
        self.repository.commit("canonical proposal")
        self.repository.git("checkout", "-q", "main")
        self.repository.write("README.md", "reviewed trusted update\n")
        base = self.repository.commit("trusted default update")
        self.repository.git("checkout", "-q", "proposal")
        self.repository.git("merge", "--no-commit", base)
        self.repository.write("untrusted.py", "raise SystemExit('do not run')\n")
        self.repository.commit("resolve trusted base merge")

        with self.assertRaisesRegex(HANDOFF.HandoffError, "unapproved path"):
            self.inspect(base_sha=base)

    def test_cache_only_review_head_can_refresh_exact_editor_input(self) -> None:
        self.repository.write(
            "source-adapters/dump-research-info/policy/curation-decisions.yaml",
            "format: orinoco-lite-curation-decisions-v1\ndecisions: {}\n",
        )
        head = self.repository.commit("record all-rejected review")

        report = self.inspect()

        self.assertEqual("canonical", report["phase"])
        self.assertEqual(head, report["head_sha"])
        self.assertEqual([], report["paths"])

    def test_automation_authored_source_proposal_is_canonical_input(self) -> None:
        self.repository.git("config", "user.name", "github-actions[bot]")
        self.repository.git(
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        )
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Automated source proposal\n",
        )
        head = self.repository.commit("source-adapter proposal")

        report = self.inspect()

        self.assertEqual("canonical", report["phase"])
        self.assertEqual(head, report["head_sha"])

    def test_non_metadata_pull_request_is_irrelevant(self) -> None:
        self.repository.write("README.md", "ordinary code review\n")
        self.repository.commit("edit docs")

        report = self.inspect()

        self.assertEqual("irrelevant", report["phase"])
        self.assertEqual([], report["paths"])


class MaterializedCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Repository(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_one_metadata_change_can_be_verified_before_and_after_commit(self) -> None:
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Materialized\n",
        )

        report = HANDOFF.inspect_materialized_changes(
            self.repository.root,
            source_commit=self.repository.base,
        )
        self.assertEqual(["metadata/records/XYZProject/example.yaml"], report["paths"])
        commit = self.repository.commit("materialize exact editor result")
        verified = HANDOFF.verify_materialized_commit(
            self.repository.root,
            source_commit=self.repository.base,
            commit=commit,
        )
        self.assertEqual(commit, verified["commit"])
        self.assertEqual(
            ["metadata/records/XYZProject/example.yaml"], verified["paths"]
        )

    def test_metadata_root_stages_without_an_annotation_tree(self) -> None:
        annotation_root = self.repository.root / "metadata/overlays/annotations"
        self.assertFalse(annotation_root.exists())
        self.repository.write(
            "metadata/records/XYZProject/example.yaml",
            "pid: https://example.test/projects/example\n"
            "schema_type: xyzri:XYZProject\n"
            "title: Materialized without annotations\n",
        )

        HANDOFF.inspect_materialized_changes(
            self.repository.root,
            source_commit=self.repository.base,
        )
        self.repository.git("add", "-A", "--", "metadata")

        staged = self.repository.git(
            "diff", "--cached", "--name-only", "--", "metadata"
        ).stdout.splitlines()
        self.assertEqual(["metadata/records/XYZProject/example.yaml"], staged)
        self.assertFalse(annotation_root.exists())

    def test_empty_or_nonmetadata_materialization_is_rejected(self) -> None:
        with self.assertRaisesRegex(HANDOFF.HandoffError, "produced no"):
            HANDOFF.inspect_materialized_changes(
                self.repository.root,
                source_commit=self.repository.base,
            )
        self.repository.write("README.md", "not canonical metadata\n")
        with self.assertRaisesRegex(HANDOFF.HandoffError, "unapproved path"):
            HANDOFF.inspect_materialized_changes(
                self.repository.root,
                source_commit=self.repository.base,
            )


class ReviewLinkTests(unittest.TestCase):
    def test_link_uses_only_configurable_origin_and_git_coordinates(self) -> None:
        self.assertEqual(
            "https://review.example.test:8443/edit/?"
            "repository=con%2Fexample&pull_request=17&expected_head_sha=" + "a" * 40,
            HANDOFF.review_url(
                "https://review.example.test:8443/",
                "con/example",
                17,
                "a" * 40,
            ),
        )

    def test_link_rejects_non_origin_and_invalid_coordinates(self) -> None:
        for origin in (
            "http://review.example.test",
            "https://user@review.example.test",
            "https://review.example.test/path",
            "https://review.example.test/?state=1",
        ):
            with self.subTest(origin=origin):
                with self.assertRaisesRegex(HANDOFF.HandoffError, "HTTPS origin"):
                    HANDOFF.review_url(origin, "con/example", 17, "a" * 40)
        with self.assertRaisesRegex(HANDOFF.HandoffError, "owner/name"):
            HANDOFF.review_url(
                "https://review.example.test", "not-a-repo", 17, "a" * 40
            )
        with self.assertRaisesRegex(HANDOFF.HandoffError, "Expected head SHA"):
            HANDOFF.review_url("https://review.example.test", "con/example", 17, "main")


if __name__ == "__main__":
    unittest.main()
