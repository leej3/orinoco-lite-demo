from __future__ import annotations

from pathlib import Path
import re
import tomllib
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/curation-review.yml"
VALIDATE_WORKFLOW = ROOT / ".github/workflows/validate.yml"
SOURCES = ROOT / "source-adapters/metadata/sources.toml"


class CurationReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.contract = yaml.load(cls.text, Loader=yaml.BaseLoader)
        cls.propose = cls.contract["jobs"]["propose"]
        cls.submit = cls.contract["jobs"]["submit"]
        cls.propose_steps = {step["name"]: step for step in cls.propose["steps"]}
        cls.submit_steps = {step["name"]: step for step in cls.submit["steps"]}
        cls.validation_contract = yaml.load(
            VALIDATE_WORKFLOW.read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )

    def test_triggers_and_permissions_are_narrow(self) -> None:
        self.assertEqual(
            {"workflow_dispatch", "issue_comment"}, set(self.contract["on"])
        )
        inputs = self.contract["on"]["workflow_dispatch"]["inputs"]
        self.assertNotIn("adapter_agent_pid", inputs)
        self.assertEqual("true", inputs["acknowledge_public_review_data"]["required"])
        self.assertEqual("false", inputs["acknowledge_public_review_data"]["default"])
        self.assertEqual({}, self.contract["permissions"])
        self.assertEqual(
            {
                "actions": "write",
                "contents": "write",
                "issues": "write",
                "pull-requests": "write",
            },
            self.propose["permissions"],
        )
        self.assertEqual(
            {"actions": "write", "contents": "write", "pull-requests": "write"},
            self.submit["permissions"],
        )
        self.assertNotIn("pull_request_target", self.text)
        self.assertNotIn("workflow_run", self.contract["on"])
        self.assertIn("pull_request", self.validation_contract["on"])
        self.assertIn("workflow_dispatch", self.validation_contract["on"])
        self.assertIn(
            "startsWith(github.event.comment.body, '/curation submit')",
            self.text,
        )

    def test_proposal_derives_one_reviewed_provenance_identity_from_policy(
        self,
    ) -> None:
        configured = tomllib.loads(SOURCES.read_text(encoding="utf-8"))
        identities = {
            source["id"]: source["provenance_identity"]
            for source in configured["sources"]
        }
        self.assertEqual(
            {
                "dump-research-info": (
                    "xyzrins:source-adapters/dump-research-info/v2"
                ),
                "zotero": "xyzrins:source-adapters/zotero/v1",
            },
            identities,
        )

        resolution = self.propose_steps[
            "Resolve the reviewed adapter provenance identity"
        ]
        self.assertEqual("source_policy", resolution["id"])
        self.assertEqual("${{ inputs.adapter }}", resolution["env"]["ADAPTER"])
        self.assertIn("source-adapters/metadata/sources.toml", resolution["run"])
        self.assertIn("metadata/tools/review.py", resolution["run"])
        self.assertIn("resolve-provenance-identity", resolution["run"])
        self.assertIn("pixi run python", resolution["run"])
        self.assertIn("GITHUB_OUTPUT", resolution["run"])
        self.assertNotIn("tomllib", resolution["run"])
        step_names = [step["name"] for step in self.propose["steps"]]
        self.assertLess(
            step_names.index("Install the trusted locked Pixi environment"),
            step_names.index("Resolve the reviewed adapter provenance identity"),
        )

        output = "${{ steps.source_policy.outputs.provenance_identity }}"
        for name in (
            "Build the ephemeral candidate plan",
            "Create one explicit DataLad proposal commit",
            "Render one untracked review bundle from the proposal",
        ):
            self.assertEqual(
                output,
                self.propose_steps[name]["env"]["PROVENANCE_IDENTITY"],
            )
        self.assertNotIn("${{ inputs.adapter_agent_pid }}", self.text)

    def test_token_created_writes_dispatch_validation_for_the_exact_refs(self) -> None:
        proposal = self.propose_steps[
            "Dispatch ordinary validation for the exact proposal ref"
        ]["run"]
        reviewed = self.submit_steps[
            "Dispatch ordinary validation for the exact reviewed ref"
        ]["run"]
        self.assertEqual(2, self.text.count("gh workflow run validate.yml"))
        self.assertIn('--ref "$BRANCH"', proposal)
        self.assertIn('--ref "$HEAD_REF"', reviewed)

    def test_actions_are_pinned_and_checkout_never_persists_credentials(self) -> None:
        references = re.findall(r"^\s*uses:\s*([^\s#]+)", self.text, re.MULTILINE)
        self.assertGreaterEqual(len(references), 2)
        for reference in references:
            self.assertRegex(reference, r"@[0-9a-f]{40}$")
        checkout_count = sum(
            reference.startswith("actions/checkout@") for reference in references
        )
        self.assertEqual(checkout_count, self.text.count("persist-credentials: false"))
        self.assertNotIn("persist-credentials: true", self.text)

    def test_proposal_is_one_inline_datalad_commit_with_both_metadata_outputs(
        self,
    ) -> None:
        step = self.propose_steps["Create one explicit DataLad proposal commit"]["run"]
        self.assertEqual(1, step.count("datalad run --explicit"))
        self.assertEqual(1, step.count('-m "'))
        self.assertIn("-o metadata/records", step)
        self.assertIn("-o metadata/overlays/annotations", step)
        self.assertIn("stage-proposal", step)
        self.assertIn("--adapter-agent-pid", step)
        self.assertIn("--metadata-base", step)
        self.assertNotIn("--sidecar", step)
        self.assertIn("--no-renames", step)
        self.assertIn('{"A", "M", "D"}', step)
        self.assertNotIn("curation-decisions.yaml", step)
        self.assertIn("Curation-Source", step)
        self.assertIn("Curation-Adapter", step)
        self.assertIn("Curation-Adapter-Agent", step)
        self.assertIn("--no-renames", step)
        self.assertIn('{"A", "M", "D"}', step)
        self.assertIn('("metadata", "records")', step)
        self.assertIn('("metadata", "overlays", "annotations")', step)

    def test_single_untracked_review_bundle_and_concise_fallback_are_published(
        self,
    ) -> None:
        plan = self.propose_steps["Build the ephemeral candidate plan"]["run"]
        bundle = self.propose_steps[
            "Render one untracked review bundle from the proposal"
        ]["run"]
        upload = self.propose_steps["Upload the single review-bundle artifact"]
        body = self.propose_steps["Render the concise editable pull-request body"][
            "run"
        ]
        pull = self.propose_steps["Open one draft pull request"]["run"]
        publish = self.propose_steps[
            "Publish the review link on the draft pull request"
        ]["run"]
        self.assertIn("inspect-plan", plan)
        self.assertIn("render-review-bundle", bundle)
        self.assertIn('--root "$base_root"', bundle)
        self.assertIn('--trusted-root "$GITHUB_WORKSPACE"', bundle)
        self.assertIn('--proposal-sha "$PROPOSAL_SHA"', bundle)
        self.assertIn('--repository "$REPOSITORY"', bundle)
        self.assertIn('--pull-request "$NUMBER"', bundle)
        self.assertIn('--workflow-run-id "$GITHUB_RUN_ID"', bundle)
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", bundle)
        self.assertIn('test "${#artifact_files[@]}" = 1', bundle)
        self.assertEqual(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            upload["uses"],
        )
        self.assertEqual(
            "orinoco-curation-review-${{ steps.proposal.outputs.sha }}",
            upload["with"]["name"],
        )
        self.assertEqual(
            "${{ runner.temp }}/review-artifact/review-bundle.json",
            upload["with"]["path"],
        )
        self.assertEqual(1, self.text.count("actions/upload-artifact@"))
        self.assertIn("--draft", pull)
        self.assertIn(
            "The trusted workflow is preparing the curation review link.",
            pull,
        )
        self.assertNotIn("AI-generated draft", self.text)
        self.assertIn("render-pr-body", body)
        self.assertIn('--root "$GITHUB_WORKSPACE"', body)
        self.assertIn('--artifact-id "$ARTIFACT_ID"', body)
        self.assertIn('--body-file "$RUNNER_TEMP/pr-body.md"', publish)
        self.assertNotIn("prototype", self.text.lower())
        self.assertNotIn("orinoco-lite-curation-form", self.text)
        self.assertNotIn("task-list", self.text)
        self.assertNotIn("candidate descriptor", self.text.lower())
        self.assertNotIn("inspect-summary", self.text)
        self.assertNotIn("render-summary", self.text)
        self.assertNotIn("--summary", self.text)
        self.assertNotIn("retention-days", str(upload))

        self.assertNotIn("CURATION_REVIEW_APP_ORIGIN", self.text)
        self.assertNotIn("orinoco-curation-review.pages.dev/review", self.text)

    def test_bot_finalization_copy_links_the_commit_and_reports_validation(self) -> None:
        final = self.submit_steps["Post the finalization result"]["run"]
        self.assertIn("https://github.com/{repository}/commit/{commit}", final)
        self.assertIn("Recorded human acceptance decisions", final)
        self.assertIn("Validation has been requested.", final)
        self.assertIn("Merge after it passes.", final)
        self.assertNotIn("no approval or merge was performed", final)
        self.assertNotIn("awaiting ordinary validation", final)

    def test_submission_uses_trusted_code_and_exact_authenticated_context(self) -> None:
        proposal = self.propose_steps["Create one explicit DataLad proposal commit"][
            "run"
        ]
        parse = self.submit_steps["Parse only the exact complete JSON submission"][
            "run"
        ]
        authority = self.submit_steps[
            "Verify the pull request and authenticated curator"
        ]["run"]
        coordinates = self.submit_steps["Inspect the submitted proposal coordinates"][
            "run"
        ]
        isolate = self.submit_steps[
            "Isolate the proposal parent and restrict later branch changes"
        ]["run"]
        rehearsal = self.submit_steps["Rebuild and rehearse the complete finalization"][
            "run"
        ]
        metadata = self.submit_steps[
            "Apply metadata-changing finalization through DataLad"
        ]["run"]
        cache = self.submit_steps["Apply a decision-cache-only finalization"]["run"]
        self.assertIn("trusted/source-adapters/metadata/tools/curation.py", parse)
        self.assertIn("inspect-submission", parse)
        self.assertIn('{"write", "admin"}', authority)
        self.assertIn('event["comment"]["user"]', authority)
        self.assertIn('event["comment"]["created_at"]', authority)
        self.assertIn('event["comment"]["html_url"]', authority)
        self.assertIn("$RUNNER_TEMP/submission.json", coordinates)
        self.assertIn('submission["proposal_sha"]', coordinates)
        self.assertIn('submission["source_coordinate"]', coordinates)
        self.assertNotIn('pull.get("body")', coordinates)
        self.assertIn("git -C trusted merge-base --is-ancestor", isolate)
        self.assertIn('trusted_head_sha="$(git -C trusted rev-parse HEAD)"', isolate)
        self.assertIn("--base-root", rehearsal)
        self.assertIn("--trusted-root", rehearsal)
        self.assertIn("--dry-run", rehearsal)
        self.assertEqual(1, rehearsal.count("--dry-run"))
        for execution in (rehearsal, metadata, cache):
            self.assertIn('--trusted-head-sha "$TRUSTED_HEAD_SHA"', execution)
        for execution in (proposal, rehearsal, metadata, cache):
            self.assertIn("unset GH_TOKEN GITHUB_TOKEN", execution)

    def test_finalization_resolves_the_exact_trusted_parent_runtime(self) -> None:
        isolate = self.submit_steps[
            "Isolate the proposal parent and restrict later branch changes"
        ]["run"]
        runtime = self.submit_steps[
            "Resolve the proposal parent's released runtime"
        ]["run"]
        validation = self.submit_steps["Validate the complete joined graph"]["run"]
        self.assertIn(
            'git -C trusted merge-base --is-ancestor "$parent" HEAD',
            isolate,
        )
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", runtime)
        self.assertIn("--frozen --clean-env", runtime)
        self.assertIn(
            '--manifest-path "${{ steps.base.outputs.root }}/pixi.toml"',
            runtime,
        )
        self.assertIn("--executable orinoco", runtime)
        self.assertNotIn("trusted/pixi.toml", runtime)
        self.assertNotIn("review/pixi.toml", runtime)
        self.assertEqual(
            2,
            self.text.count(
                '--manifest-path "${{ steps.base.outputs.root }}/pixi.toml"'
            ),
        )
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", validation)
        self.assertEqual(1, validation.count("--frozen --clean-env"))
        self.assertEqual(1, validation.count("--executable /bin/sh"))
        self.assertEqual(
            1,
            validation.count(
                '--manifest-path "${{ steps.base.outputs.root }}/pixi.toml"'
            ),
        )
        self.assertEqual(2, validation.count("run_parent_orinoco --root"))
        self.assertIn("system_git=/usr/bin/git", validation)
        self.assertIn('test -f "$system_git"', validation)
        self.assertIn('ln -s "$system_git" "$git_bin/git"', validation)
        self.assertNotIn("command -v git", validation)
        self.assertIn('export PATH="$1:$PATH"', validation)
        self.assertIn("export GIT_CONFIG_GLOBAL=/dev/null", validation)
        self.assertIn("export GIT_CONFIG_NOSYSTEM=1", validation)
        self.assertIn("export GIT_NO_REPLACE_OBJECTS=1", validation)
        self.assertIn("export GIT_TERMINAL_PROMPT=0", validation)
        self.assertIn('exec orinoco "$@"', validation)
        self.assertNotIn("trusted/pixi.toml", validation)
        self.assertNotIn("review/pixi.toml", validation)
        step_names = [step["name"] for step in self.submit["steps"]]
        validation_index = step_names.index("Validate the complete joined graph")
        for name in (
            "Rebuild and rehearse the complete finalization",
            "Apply metadata-changing finalization through DataLad",
            "Apply a decision-cache-only finalization",
        ):
            self.assertLess(step_names.index(name), validation_index)
        for name in (
            "Rebuild and rehearse the complete finalization",
            "Apply metadata-changing finalization through DataLad",
            "Apply a decision-cache-only finalization",
        ):
            execution = self.submit_steps[name]["run"]
            self.assertIn("trusted/pixi.toml", execution)
            self.assertIn(
                "trusted/source-adapters/metadata/tools/curation.py",
                execution,
            )
            self.assertNotIn("steps.base.outputs.root }}/pixi.toml", execution)

    def test_direct_metadata_commits_and_suggestions_remain_branch_data(self) -> None:
        isolate = self.submit_steps[
            "Isolate the proposal parent and restrict later branch changes"
        ]["run"]
        self.assertIn("validate-review-history", isolate)
        self.assertNotIn("--name-only", isolate)
        self.assertNotIn("curation-decisions.yaml", isolate)
        self.assertIn(
            '("Adapter", "Adapter-Agent", "Metadata-Base", "Source")', isolate
        )
        self.assertIn('fields["Adapter"] != submission["adapter"]', isolate)
        self.assertIn('fields["Metadata-Base"] != sys.argv[3]', isolate)
        self.assertIn('fields["Source"] != canonical_source', isolate)
        self.assertIn('source != submission["source_coordinate"]', isolate)
        self.assertNotIn("git reset", self.text)
        self.assertNotIn("git checkout --", self.text)
        self.assertIn("--head-sha", self.text)
        self.assertIn("finalize", self.text)

    def test_metadata_finalization_is_datalad_recorded_but_cache_only_is_not(
        self,
    ) -> None:
        metadata = self.submit_steps[
            "Apply metadata-changing finalization through DataLad"
        ]["run"]
        cache = self.submit_steps["Apply a decision-cache-only finalization"]["run"]
        self.assertIn("datalad run --explicit", metadata)
        self.assertIn("-o metadata/records", metadata)
        self.assertIn("-o metadata/overlays/annotations", metadata)
        self.assertIn('-o "$CACHE_PATH"', metadata)
        self.assertNotIn("datalad", cache)
        self.assertIn('git -C review add -- "$CACHE_PATH"', cache)
        self.assertIn("--author=", cache)
        self.assertIn("Curation-Trusted-Head: ${TRUSTED_HEAD_SHA}", cache)
        self.assertIn("github-actions[bot]", metadata)
        self.assertIn("GIT_AUTHOR_DATE", metadata)

    def test_joined_validation_and_exact_head_push_are_the_only_automation(
        self,
    ) -> None:
        validation = self.submit_steps["Validate the complete joined graph"]["run"]
        push = self.submit_steps["Push with an exact observed-head lease"]["run"]
        self.assertIn("projection update", validation)
        self.assertIn("validate", validation)
        self.assertIn("review commit omitted the compact decision cache", validation)
        self.assertNotIn("validate-diff", self.text)
        self.assertNotIn("--reserve-files", self.text)
        self.assertNotIn("MAX_GITHUB_DIFF", self.text)
        self.assertEqual(1, push.count("--force-with-lease="))
        self.assertIn("${HEAD_SHA}", push)
        self.assertIn('merge-base --is-ancestor "$HEAD_SHA" HEAD', push)
        self.assertIn('rev-list --count "${HEAD_SHA}..HEAD"', push)
        self.assertIn('show -s --format=%P HEAD)" = "$HEAD_SHA"', push)
        for forbidden in (
            "gh pr approve",
            "gh pr merge",
            "gh pr ready",
            "wrangler deploy",
            "repository: con/dump-research-info\n          token:",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_final_write_rechecks_the_complete_pull_and_comment_context(self) -> None:
        reread = self.submit_steps[
            "Re-read the exact pull request and comment before writing"
        ]["run"]
        for required in (
            "issues/comments/${COMMENT_ID}",
            "collaborators/${ACTOR}/permission",
            'repository["default_branch"]',
            'repository["full_name"]',
            '"curation-review" not in labels',
            'submitted_comment["updated_at"]',
            'comment.get("body") != captured_comment',
            '{"write", "admin"}',
        ):
            self.assertIn(required, reread)
        self.assertNotIn('current.get("body")', reread)


if __name__ == "__main__":
    unittest.main()
