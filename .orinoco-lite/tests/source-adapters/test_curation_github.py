from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from orinoco_lite.annotations import annotation_companion, assertion_sha256
from orinoco_lite.candidates import Candidate, CandidatePlan
from orinoco_lite.decisions import Disposition


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "orinoco_curation_github_host_test",
    ROOT / ".orinoco-lite/source-adapters/metadata/tools/curation.py",
)
assert SPEC is not None and SPEC.loader is not None
HOST = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HOST
SPEC.loader.exec_module(HOST)

PROPOSAL = "b" * 40
HEAD = "c" * 40
BASE = "a" * 40
TEST_RECORD_ROOT = "site-specific/metadata/records"
TEST_ANNOTATION_ROOT = "site-specific/metadata/overlays/annotations"


def record(pid: str, title: str) -> dict[str, object]:
    return {
        "pid": pid,
        "schema_type": "xyzri:XYZProject",
        "title": title,
    }


def plan() -> CandidatePlan:
    first_pid = "https://example.test/things/first"
    second_pid = "https://example.test/things/second"
    return CandidatePlan(
        adapter="dump-research-info",
        adapter_version="1",
        adapter_agent_pid="https://example.test/agents/dump-v1",
        source_namespace="https://example.test/source",
        source_coordinate={"commit": "d" * 40, "kind": "git"},
        metadata_base=BASE,
        candidates=(
            Candidate(
                source_namespace="https://example.test/source",
                source_record_id="project:first",
                pid=first_pid,
                record_path="XYZProject/first.yaml",
                baseline_record=record(first_pid, "Old first"),
                proposed_record=record(first_pid, "New first"),
                baseline_companion=None,
                proposed_companion=None,
                source_claim={"title": "New first"},
                record_root=TEST_RECORD_ROOT,
                annotation_root=TEST_ANNOTATION_ROOT,
            ),
            Candidate(
                source_namespace="https://example.test/source",
                source_record_id="project:second",
                pid=second_pid,
                record_path="XYZProject/second.yaml",
                baseline_record=None,
                proposed_record=record(second_pid, "Second"),
                baseline_companion=None,
                proposed_companion=None,
                source_claim={"title": "Second"},
                blockers=("needs a reviewed relation",),
                record_root=TEST_RECORD_ROOT,
                annotation_root=TEST_ANNOTATION_ROOT,
            ),
        ),
    )


def submission_payload(value: CandidatePlan) -> dict[str, object]:
    return {
        "format": HOST.SUBMISSION_FORMAT,
        "repository": "con/example",
        "pull_request": 17,
        "proposal_sha": PROPOSAL,
        "head_sha": HEAD,
        "adapter": value.adapter,
        "source_coordinate": dict(value.source_coordinate),
        "decisions": [
            {
                "pid": candidate.pid,
                "record_path": candidate.record_repository_path,
                "operation": candidate.operation.value,
                "disposition": "accept" if index == 0 else "defer",
            }
            for index, candidate in enumerate(value.candidates)
        ],
    }


def comment(payload: dict[str, object]) -> str:
    return (
        "/curation submit\n\n"
        "<details>\n\n"
        "<summary>Complete curation submission JSON</summary>\n\n"
        "```json\n"
        + json.dumps(payload, indent=2)
        + "\n```\n\n"
        "</details>"
    )


def legacy_comment(payload: dict[str, object]) -> str:
    return "/curation submit\n\n```json\n" + json.dumps(payload, indent=2) + "\n```"


class ReviewArtifactTests(unittest.TestCase):
    def test_bundle_has_the_exact_versioned_presentation_contract(self) -> None:
        value = plan()
        bundle = HOST.render_review_bundle(
            value,
            repository="con/example",
            pull_request=17,
            workflow_run_id=1234,
            proposal_sha=PROPOSAL,
        )

        self.assertEqual(
            {
                "format",
                "repository",
                "pull_request",
                "workflow_run_id",
                "adapter",
                "metadata_base_sha",
                "proposal_sha",
                "source_coordinate",
                "candidates",
            },
            set(bundle),
        )
        self.assertEqual(HOST.REVIEW_BUNDLE_FORMAT, bundle["format"])
        self.assertEqual(BASE, bundle["metadata_base_sha"])
        self.assertEqual(1234, bundle["workflow_run_id"])
        candidate = bundle["candidates"][0]
        self.assertEqual(
            {
                "pid",
                "friendly_id",
                "label",
                "source_namespace",
                "source_record_id",
                "record_path",
                "paths",
                "operation",
                "blockers",
                "claim_sha256",
            },
            set(candidate),
        )
        self.assertEqual("first", candidate["friendly_id"])
        self.assertEqual(["site-specific/metadata/records/XYZProject/first.yaml"], candidate["paths"])
        self.assertNotIn("before", candidate)
        self.assertNotIn("after", candidate)
        encoded = HOST.review_bundle_bytes(bundle)
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertLessEqual(len(encoded), HOST.MAX_REVIEW_BUNDLE_BYTES)

    def test_bundle_writer_enforces_the_uncompressed_application_limit(self) -> None:
        oversized = {"padding": "x" * HOST.MAX_REVIEW_BUNDLE_BYTES}

        with self.assertRaisesRegex(HOST.CurationHostError, "16 MiB"):
            HOST.review_bundle_bytes(oversized)

    def test_body_is_a_concise_editable_fallback_link_not_candidate_authority(
        self,
    ) -> None:
        dump_coordinate = {
            "commit": "d" * 40,
            "repository": "https://github.com/con/dump-research-info",
            "source_roots": {
                "data/con_site": "e" * 40,
                "data/pool_psychoinformatics_de": "f" * 40,
            },
        }
        body = HOST.render_pull_request_body(
            site_base_url="https://example.github.io/example/",
            repository="con/example",
            pull_request=17,
            artifact_id=5678,
            adapter=plan().adapter,
            source_coordinate=dump_coordinate,
        )

        self.assertTrue(
            body.startswith(
                "Automated submission from source adapter "
                "`dump-research-info`. Do not squash or rebase this branch.\n"
            )
        )
        self.assertIn(
            "https://example.github.io/example/review/?"
            "repository=con%2Fexample&pull_request=17&artifact_id=5678",
            body,
        )
        self.assertIn("Open the curation review application", body)
        self.assertIn("ephemeral GitHub Actions artifact", body)
        self.assertIn("normal retention", body)
        self.assertIn("Source coordinate", body)
        self.assertEqual(1, body.count("<details>"))
        self.assertEqual(1, body.count("<summary>Details</summary>"))
        self.assertEqual(1, body.count("</details>"))
        self.assertNotIn("<details open", body)
        self.assertNotIn("AI-generated draft", body)
        self.assertNotIn("Review artifact ID", body)
        self.assertNotIn("New first", body)
        self.assertFalse(hasattr(HOST, "parse_summary"))

        zotero_body = HOST.render_pull_request_body(
            site_base_url="https://example.github.io/example/",
            repository="con/example",
            pull_request=17,
            artifact_id=5678,
            adapter="zotero",
            source_coordinate={
                "content_sha256": f"sha256:{'0' * 64}",
                "group_id": 6197458,
                "kind": "zotero-public-library",
                "library_version": 668,
            },
        )
        self.assertTrue(
            zotero_body.startswith(
                "Automated submission from source adapter "
                "`zotero-public-library`. Do not squash or rebase this branch.\n"
            )
        )

    def test_review_url_stays_under_the_downstream_site_base_url(
        self,
    ) -> None:
        arguments = {
            "repository": "con/example",
            "pull_request": 17,
            "artifact_id": 5678,
            "adapter": plan().adapter,
            "source_coordinate": plan().source_coordinate,
        }
        self_hosted = HOST.render_pull_request_body(
            site_base_url="https://site.example.test:8443/project/",
            **arguments,
        )
        self.assertIn(
            "https://site.example.test:8443/project/review/?",
            self_hosted,
        )
        for invalid in (
            "http://site.example.test/project/",
            "https://user@site.example.test/project/",
            "https://site.example.test/project",
            "https://site.example.test/project/?state=1",
            "https://site.example.test/project/../other/",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    HOST.CurationHostError,
                    "absolute HTTPS directory URL",
                ):
                    HOST.render_pull_request_body(
                        site_base_url=invalid,
                        **arguments,
                    )


class SubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan()

    def test_exact_complete_submission_is_accepted(self) -> None:
        submission = HOST.parse_submission_comment(
            comment(submission_payload(self.plan))
        )

        self.assertEqual(
            HOST.verify_submission(
                submission,
                self.plan,
                repository="con/example",
                pull_request=17,
                proposal_sha=PROPOSAL,
                head_sha=HEAD,
            ),
            {
                self.plan.candidates[0].pid: Disposition.ACCEPT,
                self.plan.candidates[1].pid: Disposition.DEFER,
            },
        )

    def test_exact_legacy_submission_remains_accepted(self) -> None:
        submission = HOST.parse_submission_comment(
            legacy_comment(submission_payload(self.plan))
        )

        self.assertEqual("con/example", submission.repository)
        self.assertEqual(2, len(submission.decisions))

    def test_incomplete_submission_is_rejected_but_array_order_is_not_authority(
        self,
    ) -> None:
        payload = submission_payload(self.plan)
        decisions = list(payload["decisions"])
        payload["decisions"] = decisions[:1]
        incomplete = HOST.parse_submission_comment(comment(payload))
        with self.assertRaisesRegex(HOST.CurationHostError, "complete candidate"):
            HOST.verify_submission(
                incomplete,
                self.plan,
                repository="con/example",
                pull_request=17,
                proposal_sha=PROPOSAL,
                head_sha=HEAD,
            )

        payload = submission_payload(self.plan)
        payload["decisions"] = list(reversed(payload["decisions"]))
        reordered = HOST.parse_submission_comment(comment(payload))
        self.assertEqual(
            {
                self.plan.candidates[0].pid: Disposition.ACCEPT,
                self.plan.candidates[1].pid: Disposition.DEFER,
            },
            HOST.verify_submission(
                reordered,
                self.plan,
                repository="con/example",
                pull_request=17,
                proposal_sha=PROPOSAL,
                head_sha=HEAD,
            ),
        )

    def test_stale_head_source_and_proposal_are_rejected(self) -> None:
        vectors = (
            ("head_sha", "e" * 40, "head SHA"),
            ("proposal_sha", "f" * 40, "proposal SHA"),
            ("source_coordinate", {"commit": "0" * 40}, "source coordinate"),
        )
        for field, replacement, message in vectors:
            with self.subTest(field=field):
                payload = submission_payload(self.plan)
                payload[field] = replacement
                submission = HOST.parse_submission_comment(comment(payload))
                with self.assertRaisesRegex(HOST.CurationHostError, message):
                    HOST.verify_submission(
                        submission,
                        self.plan,
                        repository="con/example",
                        pull_request=17,
                        proposal_sha=PROPOSAL,
                        head_sha=HEAD,
                    )

    def test_parser_rejects_extra_fields_and_non_payload_comments(self) -> None:
        payload = submission_payload(self.plan)
        payload["reviewer"] = "browser supplied"
        with self.assertRaisesRegex(HOST.CurationHostError, "unexpected reviewer"):
            HOST.parse_submission_comment(comment(payload))
        with self.assertRaisesRegex(HOST.CurationHostError, "exact /curation submit"):
            HOST.parse_submission_comment("please /curation submit")

    def test_displayed_blocker_does_not_limit_human_disposition(self) -> None:
        blocked = self.plan.candidates[1]
        self.assertTrue(blocked.blockers)
        for disposition in Disposition:
            with self.subTest(disposition=disposition.value):
                payload = submission_payload(self.plan)
                payload["decisions"][1]["disposition"] = disposition.value
                submission = HOST.parse_submission_comment(comment(payload))

                result = HOST.verify_submission(
                    submission,
                    self.plan,
                    repository="con/example",
                    pull_request=17,
                    proposal_sha=PROPOSAL,
                    head_sha=HEAD,
                )

                self.assertIs(disposition, result[blocked.pid])


class TextEnvelopeTests(unittest.TestCase):
    def test_submission_comment_is_bounded_by_character_count(self) -> None:
        self.assertEqual(225, HOST.MAX_CANDIDATES)
        self.assertEqual(65_536, HOST.MAX_SUBMISSION_COMMENT_CHARACTERS)
        within = "\N{SNOWMAN}" * HOST.MAX_SUBMISSION_COMMENT_CHARACTERS
        oversized = within + "\N{SNOWMAN}"

        with self.assertRaisesRegex(HOST.CurationHostError, "exact /curation submit"):
            HOST.parse_submission_comment(within)

        with self.assertRaisesRegex(HOST.CurationHostError, "comment.*too large"):
            HOST.parse_submission_comment(oversized)

    def test_submission_rejects_shared_envelope_variations(self) -> None:
        payload = submission_payload(plan())
        for valid in (comment(payload), legacy_comment(payload)):
            for invalid in (
                "<!-- hidden -->\n" + valid,
                "Review this first\n" + valid,
                valid.replace("```json", "```JSON"),
                valid + "\nadditional prose",
            ):
                with self.subTest(invalid=invalid[:30]):
                    with self.assertRaisesRegex(
                        HOST.CurationHostError, "exact /curation submit"
                    ):
                        HOST.parse_submission_comment(invalid)

    def test_submission_rejects_collapsed_wrapper_variations(self) -> None:
        valid = comment(submission_payload(plan()))
        for invalid in (
            valid.replace("<details>", "<details open>"),
            valid.replace(
                "Complete curation submission JSON",
                "View complete curation submission",
            ),
            valid.replace("<details>\n\n<summary>", "<details>\n<summary>"),
            valid.replace("</summary>\n\n```json", "</summary>\n```json"),
            valid.replace("\n\n</details>", "\n</details>"),
        ):
            with self.subTest(invalid=invalid[:30]):
                with self.assertRaisesRegex(
                    HOST.CurationHostError, "exact /curation submit"
                ):
                    HOST.parse_submission_comment(invalid)


class TrustedProviderBoundaryTests(unittest.TestCase):
    def test_host_binds_candidate_paths_to_configured_record_root(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            metadata_root = Path(name)
            (metadata_root / "orinoco.yaml").write_text(
                """contract_version: 2
site:
  name: Configured candidate path test
paths:
  records: site-specific/metadata/records
""",
                encoding="utf-8",
            )
            captured: dict[str, str | None] = {}

            class Provider:
                @staticmethod
                def build_candidate_plan(_root, _output, **kwargs):
                    captured["root"] = os.environ.get("ORINOCO_ROOT")
                    captured["records"] = os.environ.get("ORINOCO_RECORDS_ROOT")
                    pid = "https://example.test/things/configured"
                    candidate = Candidate(
                        source_namespace="https://example.test/source",
                        source_record_id="project:configured",
                        pid=pid,
                        record_path="XYZProject/configured.yaml",
                        baseline_record=None,
                        proposed_record=record(pid, "Configured"),
                        baseline_companion=None,
                        proposed_companion=None,
                        source_claim={"title": "Configured"},
                    )
                    return CandidatePlan(
                        adapter="dump-research-info",
                        adapter_version="1",
                        adapter_agent_pid=kwargs["adapter_agent_pid"],
                        source_namespace="https://example.test/source",
                        source_coordinate={"commit": "d" * 40, "kind": "git"},
                        metadata_base=kwargs["metadata_base"],
                        candidates=(candidate,),
                    )

            inherited = {
                "ORINOCO_ROOT": "/tmp/forged-root",
                "ORINOCO_RECORDS_ROOT": "/tmp/forged-root/metadata/records",
            }
            with (
                mock.patch.dict(os.environ, inherited, clear=False),
                mock.patch.object(HOST, "_head", return_value=BASE),
                mock.patch.object(HOST, "_load_provider", return_value=Provider),
                mock.patch.object(HOST, "_schema", return_value=object()),
            ):
                result = HOST.build_plan(
                    metadata_root,
                    metadata_root,
                    adapter="dump-research-info",
                    metadata_base=BASE,
                    adapter_agent_pid="https://example.test/agents/dump-v1",
                    runtime_root=Path("/tmp/runtime"),
                    scratch=metadata_root / "build/curation",
                    source_checkout=Path("/tmp/source"),
                    source_revision="d" * 40,
                )
                self.assertEqual(inherited["ORINOCO_ROOT"], os.environ["ORINOCO_ROOT"])
                self.assertEqual(
                    inherited["ORINOCO_RECORDS_ROOT"],
                    os.environ["ORINOCO_RECORDS_ROOT"],
                )

            self.assertEqual(str(metadata_root.resolve()), captured["root"])
            self.assertEqual(
                str((metadata_root / "site-specific/metadata/records").resolve()),
                captured["records"],
            )
            self.assertEqual(
                "site-specific/metadata/records/XYZProject/configured.yaml",
                result.candidates[0].record_repository_path,
            )

    def test_provider_progress_cannot_contaminate_machine_readable_stdout(
        self,
    ) -> None:
        value = plan()
        metadata_root = Path("/tmp/metadata-root")
        workspace = mock.Mock(root=metadata_root.resolve())
        workspace.path.return_value = (
            metadata_root / "site-specific/metadata/records"
        ).resolve()

        class Provider:
            @staticmethod
            def build_candidate_plan(*_args, **_kwargs):
                print("provider progress")
                return value

        stdout = StringIO()
        stderr = StringIO()
        with (
            mock.patch.object(HOST, "_head", return_value=BASE),
            mock.patch.object(HOST, "_load_provider", return_value=Provider),
            mock.patch.object(HOST, "_schema", return_value=object()),
            mock.patch.object(HOST, "load_workspace", return_value=workspace),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = HOST.build_plan(
                metadata_root,
                Path("/tmp/trusted-root"),
                adapter="dump-research-info",
                metadata_base=BASE,
                adapter_agent_pid=value.adapter_agent_pid,
                runtime_root=Path("/tmp/runtime"),
                scratch=Path("/tmp/metadata-root/build/curation"),
                source_checkout=Path("/tmp/source"),
                source_revision="d" * 40,
            )

        self.assertIs(result, value)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("provider progress\n", stderr.getvalue())

    def test_host_passes_separate_trusted_and_metadata_roots(self) -> None:
        value = plan()
        metadata_root = Path("/tmp/metadata-root")
        trusted_root = Path("/tmp/trusted-root")
        scratch = metadata_root / "build/curation"
        captured: dict[str, object] = {}
        workspace = mock.Mock(root=metadata_root.resolve())
        workspace.path.return_value = (
            metadata_root / "site-specific/metadata/records"
        ).resolve()

        class Provider:
            @staticmethod
            def build_candidate_plan(root, output, **kwargs):
                captured.update(root=root, output=output, **kwargs)
                return value

        with (
            mock.patch.object(HOST, "_head", return_value=BASE),
            mock.patch.object(HOST, "_load_provider", return_value=Provider),
            mock.patch.object(HOST, "_schema", return_value=object()),
            mock.patch.object(HOST, "load_workspace", return_value=workspace),
        ):
            result = HOST.build_plan(
                metadata_root,
                trusted_root,
                adapter="dump-research-info",
                metadata_base=BASE,
                adapter_agent_pid=value.adapter_agent_pid,
                runtime_root=Path("/tmp/runtime"),
                scratch=scratch,
                source_checkout=Path("/tmp/source"),
                source_revision="d" * 40,
            )

        self.assertIs(result, value)
        self.assertEqual(captured["root"], metadata_root.resolve())
        self.assertEqual(captured["trusted_root"], trusted_root.resolve())
        self.assertEqual(captured["metadata_base"], BASE)

    def test_final_plan_is_bound_to_the_exact_trusted_checkout(self) -> None:
        trusted_root = Path("/tmp/trusted-root")
        with mock.patch.object(HOST, "_head", return_value=HEAD):
            self.assertEqual(HEAD, HOST.verify_trusted_head(trusted_root, HEAD))
            with self.assertRaisesRegex(
                HOST.CurationHostError,
                "Trusted checkout HEAD differs",
            ):
                HOST.verify_trusted_head(trusted_root, PROPOSAL)


class ProposalValidationBoundaryTests(unittest.TestCase):
    def test_stage_success_requires_structural_and_joined_validation(self) -> None:
        value = plan()
        root = Path("/tmp/proposal-root")
        runtime = Path("/tmp/verified-runtime")
        workspace = object()
        calls: list[str] = []

        with (
            mock.patch.object(
                HOST,
                "stage_plan",
                side_effect=lambda *_: calls.append("stage") or ("changed.yaml",),
            ),
            mock.patch.object(
                HOST,
                "load_workspace",
                side_effect=lambda *_: calls.append("load") or workspace,
            ),
            mock.patch.object(
                HOST,
                "validate_workspace",
                side_effect=lambda *_: calls.append("structural"),
            ),
            mock.patch.object(
                HOST,
                "validate_semantics",
                side_effect=lambda *_: calls.append("joined"),
            ) as semantic,
        ):
            changed = HOST.stage_validated_plan(root, value, runtime)

        self.assertEqual(("changed.yaml",), changed)
        self.assertEqual(["stage", "load", "structural", "joined"], calls)
        semantic.assert_called_once_with(workspace, runtime.resolve())

    def test_invalid_joined_metadata_prevents_a_successful_stage_result(self) -> None:
        value = plan()
        calls: list[str] = []

        with (
            mock.patch.object(
                HOST,
                "stage_plan",
                side_effect=lambda *_: calls.append("stage") or ("changed.yaml",),
            ),
            mock.patch.object(HOST, "load_workspace", return_value=object()),
            mock.patch.object(
                HOST,
                "validate_workspace",
                side_effect=lambda *_: calls.append("structural"),
            ),
            mock.patch.object(
                HOST,
                "validate_semantics",
                side_effect=RuntimeError("invalid joined metadata"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid joined metadata"):
                HOST.stage_validated_plan(
                    Path("/tmp/proposal-root"),
                    value,
                    Path("/tmp/verified-runtime"),
                )

        self.assertEqual(["stage", "structural"], calls)


class FinalizationValidationBoundaryTests(unittest.TestCase):
    def arguments(self, root: Path) -> dict[str, object]:
        value = plan()
        return {
            "review_root": root,
            "plan": value,
            "submission": mock.sentinel.submission,
            "repository": "con/example",
            "pull_request": 17,
            "proposal_sha": PROPOSAL,
            "head_sha": HEAD,
            "reviewer": "https://github.com/reviewer",
            "reviewed_at": "2026-08-24T12:00:00Z",
            "review_url": ("https://github.com/con/example/pull/17#issuecomment-123"),
            "review_ref": "github-comment:123",
            "base_cache": mock.sentinel.base_cache,
            "runtime_root": Path("/tmp/verified-runtime"),
        }

    def patches(self, root: Path, events: list[str], *, invalid: bool = False):
        value = plan()
        dispositions = {
            value.candidates[0].pid: Disposition.ACCEPT,
            value.candidates[1].pid: Disposition.DEFER,
        }
        result = mock.Mock(metadata_changed=True, changed_paths=("metadata/change",))

        def validate(review_root, runtime_root):
            cache = (
                review_root
                / "site-specific/curation-records/dump-research-info.yaml"
            )
            self.assertEqual(b"validated cache bytes", cache.read_bytes())
            events.append("validate")
            if invalid:
                raise RuntimeError("invalid joined finalization")

        return (
            mock.patch.object(HOST, "verify_submission", return_value=dispositions),
            mock.patch.object(HOST, "verify_proposal_commit"),
            mock.patch.object(
                HOST,
                "finalize_candidate_plan",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("metadata") or result
                ),
            ),
            mock.patch.object(
                HOST,
                "update_decision_cache",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("cache") or mock.sentinel.updated_cache
                ),
            ),
            mock.patch.object(
                HOST,
                "serialize_decision_cache",
                side_effect=lambda *_: (
                    events.append("serialize") or b"validated cache bytes"
                ),
            ),
            mock.patch.object(
                HOST,
                "_validate_joined_workspace",
                side_effect=validate,
            ),
        )

    def test_finalizer_validates_after_metadata_and_cache_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            events: list[str] = []
            with ExitStack() as stack:
                for patcher in self.patches(root, events):
                    stack.enter_context(patcher)
                report = HOST._apply_finalization(**self.arguments(root))

        self.assertTrue(report["metadata_changed"])
        self.assertEqual(["metadata", "cache", "serialize", "validate"], events)

    def test_invalid_joined_finalization_cannot_return_commit_worthy_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            events: list[str] = []
            with ExitStack() as stack:
                for patcher in self.patches(root, events, invalid=True):
                    stack.enter_context(patcher)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "invalid joined finalization",
                ):
                    HOST._apply_finalization(**self.arguments(root))

        self.assertEqual(["metadata", "cache", "serialize", "validate"], events)


class ProposalCommitTests(unittest.TestCase):
    @staticmethod
    def git(root: Path, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments], text=True
        ).strip()

    def test_stage_and_verify_cover_add_modify_delete_and_companion(self) -> None:
        agent = "https://example.test/agents/dump-v1"
        imported_from = "https://example.test/source/modified"
        modified_pid = "https://example.test/things/modified"
        old_assertion = {
            "predicate": "dcterms:title",
            "schema_type": "dlthings:AttributeSpecification",
            "value": "Old",
        }
        new_assertion = {**old_assertion, "value": "New"}

        def companion(assertion):
            return annotation_companion(
                modified_pid,
                [
                    {
                        "path": "/attributes",
                        "assertion_sha256": assertion_sha256(assertion),
                        "pav:importedBy": agent,
                        "pav:importedFrom": imported_from,
                    }
                ],
            )

        modified = Candidate(
            source_namespace="https://example.test/source",
            source_record_id="modified",
            pid=modified_pid,
            record_path="XYZProject/modified.yaml",
            baseline_record={
                "pid": modified_pid,
                "schema_type": "xyzri:XYZProject",
                "title": "Old",
                "attributes": [old_assertion],
            },
            proposed_record={
                "pid": modified_pid,
                "schema_type": "xyzri:XYZProject",
                "title": "Old",
                "attributes": [new_assertion],
            },
            baseline_companion=companion(old_assertion),
            proposed_companion=companion(new_assertion),
            source_claim={"attributes": [new_assertion]},
            record_root=TEST_RECORD_ROOT,
            annotation_root=TEST_ANNOTATION_ROOT,
        )
        added_pid = "https://example.test/things/added"
        added = Candidate(
            source_namespace="https://example.test/source",
            source_record_id="added",
            pid=added_pid,
            record_path="XYZProject/added.yaml",
            baseline_record=None,
            proposed_record=record(added_pid, "Added"),
            baseline_companion=None,
            proposed_companion=None,
            source_claim={"title": "Added"},
            record_root=TEST_RECORD_ROOT,
            annotation_root=TEST_ANNOTATION_ROOT,
        )
        deleted_pid = "https://example.test/things/deleted"
        deleted = Candidate(
            source_namespace="https://example.test/source",
            source_record_id="deleted",
            pid=deleted_pid,
            record_path="XYZProject/deleted.yaml",
            baseline_record=record(deleted_pid, "Deleted"),
            proposed_record=None,
            baseline_companion=None,
            proposed_companion=None,
            source_claim={},
            record_root=TEST_RECORD_ROOT,
            annotation_root=TEST_ANNOTATION_ROOT,
        )

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.git(root, "init", "-b", "main")
            self.git(root, "config", "user.name", "Fixture")
            self.git(root, "config", "user.email", "fixture@example.test")
            for candidate in (modified, deleted):
                for change in candidate.file_changes():
                    if change.baseline is None:
                        continue
                    path = root / change.path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(change.baseline)
            self.git(root, "add", "site-specific/metadata")
            self.git(root, "commit", "-m", "test: create metadata base")
            base = self.git(root, "rev-parse", "HEAD")
            value = CandidatePlan(
                adapter="dump-research-info",
                adapter_version="1",
                adapter_agent_pid=agent,
                source_namespace="https://example.test/source",
                source_coordinate={"commit": "d" * 40},
                metadata_base=base,
                candidates=(modified, added, deleted),
            )
            bundle = HOST.render_review_bundle(
                value,
                repository="con/example",
                pull_request=17,
                workflow_run_id=1234,
                proposal_sha=PROPOSAL,
            )
            modified_bundle = next(
                item for item in bundle["candidates"] if item["pid"] == modified_pid
            )
            self.assertEqual(
                [
                    "site-specific/metadata/overlays/annotations/XYZProject/modified.yaml",
                    "site-specific/metadata/records/XYZProject/modified.yaml",
                ],
                modified_bundle["paths"],
            )

            changed = HOST.stage_plan(root, value)
            self.assertIn("site-specific/metadata/records/XYZProject/added.yaml", changed)
            self.assertIn("site-specific/metadata/records/XYZProject/deleted.yaml", changed)
            self.assertIn(
                "site-specific/metadata/overlays/annotations/XYZProject/modified.yaml", changed
            )
            self.assertFalse(
                (
                    root
                    / "site-specific/curation-records/dump-research-info.yaml"
                ).exists()
            )
            self.git(root, "add", "-A", "site-specific/metadata")
            self.git(root, "commit", "-m", "test: capture metadata proposal")
            proposal = self.git(root, "rev-parse", "HEAD")

            HOST.verify_proposal_commit(root, value, proposal)
            record_operations = {
                path: {"A": "add", "M": "modify", "D": "delete"}[status]
                for status, path in HOST._diff_entries(root, base, proposal)
                if path.startswith("site-specific/metadata/records/")
            }
            self.assertEqual(
                {
                    candidate.record_repository_path: candidate.operation.value
                    for candidate in value.candidates
                },
                record_operations,
            )
            forged_path = root / ".orinoco-lite/source-adapters/forged.py"
            forged_path.parent.mkdir(parents=True)
            forged_path.write_text("raise SystemExit('forged')\n", encoding="utf-8")
            self.git(root, "add", forged_path.relative_to(root).as_posix())
            self.git(root, "commit", "--amend", "--no-edit")
            forged = self.git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(
                HOST.CurationHostError,
                "Proposal commit paths do not match the candidate plan",
            ):
                HOST.verify_proposal_commit(root, value, forged)


class ReviewHistoryTests(unittest.TestCase):
    @staticmethod
    def git(root: Path, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments], text=True
        ).strip()

    def create_repository(self, root: Path) -> str:
        self.git(root, "init", "-b", "main")
        self.git(root, "config", "user.name", "Fixture")
        self.git(root, "config", "user.email", "fixture@example.test")
        files = {
            ".orinoco-lite/source-adapters/tool.py": "print('trusted')\n",
            "site-specific/curation-records/dump-research-info.yaml": (
                "format: orinoco-lite-curation-decisions-v1\n"
            ),
            "site-specific/metadata/records/Thing/one.yaml": "pid: https://example.test/one\n",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", "test: create base")
        record_path = root / "site-specific/metadata/records/Thing/one.yaml"
        record_path.write_text(
            "pid: https://example.test/one\ntitle: Proposed\n",
            encoding="utf-8",
        )
        self.git(root, "add", "site-specific/metadata/records/Thing/one.yaml")
        self.git(root, "commit", "-m", "test: propose metadata")
        return self.git(root, "rev-parse", "HEAD")

    def test_rename_from_code_into_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            proposal = self.create_repository(root)
            destination = "site-specific/metadata/records/Thing/copied-code.yaml"
            self.git(root, "mv", ".orinoco-lite/source-adapters/tool.py", destination)
            self.git(root, "commit", "-m", "test: disguise code rename")

            with self.assertRaisesRegex(
                HOST.CurationHostError, "changes code or workflow-owned state"
            ):
                HOST.validate_review_history(
                    root,
                    proposal,
                    self.git(root, "rev-parse", "HEAD"),
                )

    def test_rename_from_compact_cache_into_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            proposal = self.create_repository(root)
            cache = "site-specific/curation-records/dump-research-info.yaml"
            destination = "site-specific/metadata/overlays/annotations/Thing/cache.yaml"
            (root / destination).parent.mkdir(parents=True, exist_ok=True)
            self.git(root, "mv", cache, destination)
            self.git(root, "commit", "-m", "test: disguise cache rename")

            with self.assertRaisesRegex(
                HOST.CurationHostError, "changes code or workflow-owned state"
            ):
                HOST.validate_review_history(
                    root,
                    proposal,
                    self.git(root, "rev-parse", "HEAD"),
                )


if __name__ == "__main__":
    unittest.main()
