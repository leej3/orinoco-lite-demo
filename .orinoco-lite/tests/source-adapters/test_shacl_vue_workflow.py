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
        cls.default_job = cls.contract["jobs"]["default-editor"]
        cls.steps = {step["name"]: step for step in cls.job["steps"]}

    def test_triggers_and_permissions_keep_execution_on_trusted_code(self) -> None:
        self.assertEqual(
            {"push", "pull_request_target", "workflow_dispatch"},
            set(self.contract["on"]),
        )
        self.assertEqual(
            ["opened", "reopened", "synchronize"],
            self.contract["on"]["pull_request_target"]["types"],
        )
        self.assertEqual("", self.contract["on"]["push"])
        self.assertEqual({}, self.contract["permissions"])
        self.assertEqual(
            {
                "actions": "write",
                "contents": "write",
                "pull-requests": "write",
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
        self.assertIn("git -C source add -A -- metadata", commit)
        self.assertNotIn(
            "metadata/records metadata/overlays/annotations",
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

    def test_canonical_validation_and_service_link_are_exact_and_idempotent(
        self,
    ) -> None:
        validate = self.steps["Validate the exact canonical joined metadata graph"][
            "run"
        ]
        link = self.steps[
            "Re-read the canonical head and add the curation-service link"
        ]
        self.assertIn("git -C proposal rev-parse HEAD)", validate)
        self.assertIn('--root "$GITHUB_WORKSPACE/proposal" projection update', validate)
        self.assertIn('--root "$GITHUB_WORKSPACE/proposal" validate', validate)
        self.assertEqual(
            "${{ vars.CURATION_REVIEW_APP_ORIGIN || "
            "'https://orinoco-curation-review.pages.dev/' }}",
            link["env"]["REVIEW_APP_ORIGIN"],
        )
        run = link["run"]
        self.assertIn('pull.get("head", {}).get("sha")', run)
        self.assertIn('--expected-head-sha "$HEAD_SHA"', run)
        helper = HELPER.read_text(encoding="utf-8")
        self.assertIn("/edit/?{query}", helper)
        self.assertIn("--paginate --slurp", run)
        self.assertIn("orinoco-shacl-vue-proposal", run)
        self.assertIn("**AI-generated draft — not reviewed by John**", run)

    def test_editor_input_artifacts_contain_only_exact_head_presentation_data(
        self,
    ) -> None:
        build = self.steps["Build exact-head SHACL Vue presentation input"]["run"]
        upload = self.steps["Upload the expiring exact-head SHACL Vue input"]
        for path in (
            "edit/config.json",
            "edit/records.ttl",
            "edit/data/record-sources.json",
        ):
            self.assertIn(path, build)
        self.assertIn('catalog.get("source_commit") != sys.argv[2]', build)
        self.assertEqual(
            "orinoco-shacl-vue-input-${{ steps.coordinates.outputs.head_sha }}",
            upload["with"]["name"],
        )
        self.assertEqual(
            "${{ runner.temp }}/shacl-vue-editor-input/", upload["with"]["path"]
        )
        self.assertNotIn("shacl-vue-review-bundle.json", upload["with"]["path"])
        self.assertEqual("steps.inspect.outputs.phase == 'canonical'", upload["if"])
        link = self.steps[
            "Re-read the canonical head and add the curation-service link"
        ]["run"]
        self.assertNotIn("collaborators/${CURATOR}/permission", link)

        self.assertEqual({"contents": "read"}, self.default_job["permissions"])
        self.assertIn(
            "github.ref_name == github.event.repository.default_branch",
            self.default_job["if"],
        )
        default_steps = {step["name"]: step for step in self.default_job["steps"]}
        checkout = default_steps["Check out exact trusted default head"]
        self.assertEqual("${{ github.sha }}", checkout["with"]["ref"])
        self.assertEqual("false", checkout["with"]["persist-credentials"])
        default_upload = default_steps[
            "Upload the expiring exact-default SHACL Vue input"
        ]
        self.assertEqual(
            "orinoco-shacl-vue-input-${{ github.sha }}",
            default_upload["with"]["name"],
        )

    def test_token_created_curation_heads_dispatch_exact_editor_input(self) -> None:
        curation = (ROOT / ".github/workflows/curation-review.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(2, curation.count("gh workflow run shacl-vue-proposal.yml"))
        self.assertIn('-f "expected_head=${HEAD_SHA}"', curation)
        self.assertIn('-f "expected_head=${head_sha}"', curation)

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
