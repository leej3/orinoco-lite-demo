from __future__ import annotations

from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/shacl-vue-proposal.yml"
HELPER = ROOT / ".orinoco-lite/tools/shacl_vue_handoff.py"


class ShaclVueWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.contract = yaml.load(cls.text, Loader=yaml.BaseLoader)
        cls.job = cls.contract["jobs"]["validate"]
        cls.steps = {step["name"]: step for step in cls.job["steps"]}

    def test_triggers_and_permissions_keep_execution_on_trusted_code(self) -> None:
        self.assertEqual(
            {"pull_request_target", "workflow_dispatch"},
            set(self.contract["on"]),
        )
        self.assertEqual(
            ["opened", "reopened", "synchronize"],
            self.contract["on"]["pull_request_target"]["types"],
        )
        self.assertEqual({}, self.contract["permissions"])
        self.assertEqual(
            {
                "actions": "write",
                "contents": "write",
                "pull-requests": "read",
            },
            self.job["permissions"],
        )
        self.assertEqual("false", self.job["concurrency"]["cancel-in-progress"])
        condition = self.job["if"]
        self.assertIn("pull_request.draft", condition)
        self.assertIn("repository.default_branch", condition)
        self.assertIn("head.repo.full_name == github.repository", condition)
        trusted = self.steps["Check out exact trusted default-branch code"]
        proposal = self.steps["Check out exact proposal head as data"]
        self.assertEqual(
            "${{ steps.coordinates.outputs.base_sha }}", trusted["with"]["ref"]
        )
        self.assertEqual(
            "${{ steps.coordinates.outputs.head_sha }}", proposal["with"]["ref"]
        )
        for checkout in (trusted, proposal):
            self.assertEqual("false", checkout["with"]["persist-credentials"])

    def test_handoff_requires_exact_actor_authority_history_and_fixed_path(self) -> None:
        resolve = self.steps["Resolve exact pull request and attributed head"]["run"]
        enforce = self.steps["Enforce the authenticated exact-head handoff boundary"][
            "run"
        ]
        classify = self.steps["Classify the fixed handoff or canonical metadata head"][
            "run"
        ]
        self.assertIn('author.get("type") == "User"', resolve)
        self.assertIn('os.environ["EVENT_SENDER"] == author["login"]', resolve)
        self.assertIn('[[ "$EVENT_AUTHOR_MATCH" == "true" ]]', enforce)
        self.assertNotIn("IS_CURATION", enforce)
        self.assertNotIn('[[ "$PARENT_SHA" == "$BASE_SHA" ]]', enforce)
        self.assertIn("shacl_vue_handoff.py", classify)
        self.assertIn('--head-sha "$HEAD_SHA"', classify)
        self.assertIn('--base-sha "$BASE_SHA"', classify)
        self.assertIn(
            ".orinoco-lite/shacl-vue-review-bundle.json",
            HELPER.read_text(encoding="utf-8"),
        )
        authority = self.steps["Verify the attributed curator remains authorized"]
        boundary = self.steps["Enforce the authenticated exact-head handoff boundary"]
        self.assertEqual("steps.inspect.outputs.phase == 'handoff'", authority["if"])
        self.assertEqual("steps.inspect.outputs.phase == 'handoff'", boundary["if"])

    def test_trusted_python_applies_validates_and_materializes_same_parent(
        self,
    ) -> None:
        apply = self.steps["Extract and apply the unchanged version 2 bundle"]["run"]
        validate = self.steps["Validate the materialized joined graph"]["run"]
        commit = self.steps["Create the equivalent attributed human metadata commit"][
            "run"
        ]
        self.assertIn("trusted/pixi.toml", apply)
        self.assertIn("editor apply", apply)
        self.assertIn('"$RUNNER_TEMP/shacl-vue-review-bundle.json" --write', apply)
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", apply)
        self.assertIn('--root "$GITHUB_WORKSPACE/source" projection update', validate)
        self.assertIn('--root "$GITHUB_WORKSPACE/source" validate', validate)
        self.assertIn('GIT_AUTHOR_NAME="$CURATOR"', commit)
        self.assertIn('GIT_COMMITTER_NAME="github-actions[bot]"', commit)
        self.assertIn('--source-commit "$SOURCE_COMMIT"', commit)
        self.assertIn("verify-commit", commit)
        self.assertIn("git -C source add -A -- site-specific/metadata", commit)
        self.assertNotIn(
            "site-specific/metadata/records site-specific/metadata/overlays/annotations",
            commit,
        )

    def test_replacement_is_one_exact_lease_then_retriggers_validation(self) -> None:
        push = self.steps["Replace only the exact handoff head with a lease"]["run"]
        retrigger = self.steps["Retrigger trusted validation at the replacement head"][
            "run"
        ]
        self.assertIn('pull.get("head", {}).get("sha")', push)
        self.assertIn('permission.get("permission") not in {"write", "admin"}', push)
        self.assertIn(
            '--force-with-lease="refs/heads/${HEAD_REF}:${HANDOFF_SHA}"', push
        )
        self.assertIn('"${REPLACEMENT_SHA}:refs/heads/${HEAD_REF}"', push)
        self.assertIn("gh workflow run shacl-vue-proposal.yml", retrigger)
        self.assertIn('-f "expected_head=${REPLACEMENT_SHA}"', retrigger)

    def test_canonical_validation_is_exact_and_does_not_rehost_the_editor(
        self,
    ) -> None:
        validate = self.steps["Validate the exact canonical joined metadata graph"][
            "run"
        ]
        self.assertIn("git -C proposal rev-parse HEAD)", validate)
        self.assertIn('--root "$GITHUB_WORKSPACE/proposal" projection update', validate)
        self.assertIn('--root "$GITHUB_WORKSPACE/proposal" validate', validate)
        self.assertEqual({"validate"}, set(self.contract["jobs"]))
        for obsolete in (
            "actions/upload-artifact@",
            "shacl-vue-editor-input",
            "CURATION_REVIEW_APP_ORIGIN",
            "orinoco-shacl-vue-input-",
            "review-url",
        ):
            self.assertNotIn(obsolete, self.text)
        helper = HELPER.read_text(encoding="utf-8")
        self.assertNotIn("review-url", helper)
        self.assertNotIn("/edit/?", helper)

    def test_profile_and_helper_are_adapter_neutral(self) -> None:
        combined = self.text + HELPER.read_text(encoding="utf-8")
        self.assertNotIn("dump-research-info", combined)
        self.assertNotIn("zotero", combined)
        self.assertIn("curation-records", combined)

    def test_profile_never_executes_head_or_adds_adapter_decision_behavior(
        self,
    ) -> None:
        references = re.findall(r"^\s*uses:\s*([^\s#]+)", self.text, re.MULTILINE)
        for reference in references:
            self.assertRegex(reference, r"@[0-9a-f]{40}$")
        self.assertNotIn("proposal/pixi.toml", self.text)
        self.assertNotIn("proposal/source-adapters", self.text)
        for forbidden in (
            "datalad",
            "decision-cache",
            "disposition",
            "gh pr approve",
            "gh pr merge",
            "gh pr ready",
            "wrangler",
            "source_revision",
        ):
            self.assertNotIn(forbidden, self.text.lower())


if __name__ == "__main__":
    unittest.main()
