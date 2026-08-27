# GitHub source curation

This repository implements the trusted downstream side of the normative [GitHub curation profile][profile].
The pull request and its proposal commit contain the actual record and annotation-companion changes.
Neither the pull- request body nor the review artifact is candidate or decision authority.

Run **Curate source metadata** from the default branch to open a draft proposal.
The workflow derives the selected source's reviewed adapter provenance identity
from trusted `sources.toml`; an operator neither supplies nor overrides it.
The candidate provider requires the corresponding reviewed
`xyzri:XYZInstrument` record in `metadata/records/`, so a missing policy or
record fails closed before a proposal is opened.
The caller must acknowledge that public proposal data, authenticated review identity, and decisions remain visible in GitHub history.

## Proposal and review artifact

The proposal is one inline `datalad run --explicit` commit whose declared outputs are `metadata/records/` and `metadata/overlays/annotations/`.
Its message records exactly one `Curation-Adapter`, `Curation-Adapter-Agent`, `Curation-Metadata-Base`, and canonical `Curation-Source` field above the inline DataLad run record.
The Action resolves the released runtime, constructs the pinned Things `SchemaView`, and executes adapter and host code only from the trusted default branch.
The proposal-parent checkout and exact external source checkout are data.
Before the DataLad command returns, it runs the shared workspace checks and joined semantic validation against that verified runtime, without changing projection files.

After opening the draft pull request, the workflow regenerates and verifies the plan from the proposal parent, then uploads exactly one Actions artifact named `orinoco-curation-review-<proposal-sha>`.
Its ZIP contains one top-level `review-bundle.json` with format `orinoco-lite-curation-review-bundle-v1`.
The object has exactly these fields:

```text
format, repository, pull_request, workflow_run_id, adapter,
metadata_base_sha, proposal_sha, source_coordinate, candidates
```

Each candidate has exactly `pid`, `friendly_id`, `label`, `source_namespace`, `source_record_id`, `record_path`, `paths`, `operation`, `blockers`, and `claim_sha256`.
`paths` contains the exact record and optional annotation-companion paths from that candidate.
Record contents stay in Git; the application loads before and current bytes from the named commits.
The bundle is untracked, reproducible presentation data, expires under the repository's normal Actions retention, and is never read by finalization.
The producer rejects canonical JSON above 16 MiB.
The application also rejects an artifact ZIP above 8 MiB.
`upload-artifact` does not expose its eventual ZIP size before upload, so the producer does not attempt to emulate that archive with a brittle second compressor; the application enforces the compressed download boundary.

The concise pull-request body links to the bundle through the configured review application, gives the source coordinate, explains artifact and Git retention, and states the merge-history rule.
It is an editable accessible fallback, so the Action does not parse or re-render it.
The central application origin is `https://orinoco-curation-review.pages.dev/`.
A downstream may set the repository variable `CURATION_REVIEW_APP_ORIGIN` to a self-hosted HTTPS origin with no path, query, fragment, or user information.
The origin changes only the generated link; it has no decision authority.

## Human review and trusted finalization

Reviewers may use the hosted application, inspect or edit the diff in GitHub, apply GitHub suggestions, or push ordinary metadata commits.
GitHub attributes suggestions and direct commits, and neither path requires a local checkout.
The hosted application posts one complete authenticated comment whose first line is `/curation submit`.
The JSON is inside an exact Markdown `details` element that is closed by default, keeping the pull-request conversation readable while the full comment remains durable in GitHub and the resulting decision remains in commit history.
The trusted host also accepts the former exact unwrapped fenced-JSON envelope for historical comments and event replays; no other wrapper variations are accepted.
Its JSON is bound to the repository, pull request, proposal SHA, current head, adapter, exact source coordinate, and complete candidate mapping.
Decision-array order is not authority.

The comment Action derives the reviewer from the GitHub event and requires `write` or `admin` access.
It reads the adapter, metadata base, and source coordinate from the proposal commit, regenerates the candidate plan from that base and exact source with trusted code, and verifies the proposal's actual add, modify, and delete paths and bytes.
It rejects stale or incomplete submissions and later history containing any non-record or non-annotation path, including a rename whose destination alone is an allowed metadata path.
It also binds the operation to the exact trusted default-checkout SHA.
DataLad records that SHA as a command argument; a cache-only commit records it as `Curation-Trusted-Head`.
The pull-request body and artifact are not inputs to these checks.

Metadata-changing finalization is another explicit DataLad commit covering both metadata trees and the compact adapter cache.
A cache-only finalization is an ordinary Git commit.
In both cases the authenticated human is the author, automation is the committer, and the complete joined graph is validated after applying metadata and cache bytes but before DataLad or Git can commit them.
Immediately before writing, the Action re-reads the complete pull-request, authenticated comment, and collaborator permission predicates.
It then proves there is exactly one direct-child finalization commit and pushes with a lease for the exact observed head.

The workflow never chooses a disposition, marks the pull request ready, approves, merges, deploys, or writes to a source.
Curation pull requests must be merged with merge commits so proposal and human-review commits survive.

## Distinct SHACL Vue human edits

SHACL Vue GitHub proposal editing follows the separate normative [human-edit profile][human-edit-profile], not the source-adapter decision workflow.
SHACL Vue still downloads its normal `orinoco-shacl-review-bundle` version 2 object.
After an explicit authenticated **Propose via GitHub**, the thin application wrapper appends those exact bytes as one temporary commit that adds only `.orinoco-lite/shacl-vue-review-bundle.json`.
The commit must be the exact head of a same-repository draft pull request, its one parent and the bundle `source_commit` must match, and GitHub must attribute it to a collaborator with `write` or `admin` access.

`Materialize SHACL Vue proposal` runs with `pull_request_target` code from the reviewed default branch and never executes proposal bytes.
It applies the bundle with the pinned Orinoco Python editor behavior against an isolated checkout of the exact parent, permits output only in `metadata/records/` and `metadata/overlays/annotations/`, and validates the complete joined graph.
It then creates one ordinary commit with that same parent, the authenticated curator as author, and automation as committer.
An exact `force-with-lease` replaces only the temporary head, so every earlier proposal or human commit survives and the branch retains canonical YAML rather than the RDF transport.

The workflow retriggers itself at the exact replacement SHA, revalidates canonical metadata, and adds a configurable `/edit` curation-service link bound to the repository, pull request, and exact canonical head idempotently.
For each validated canonical pull-request head it also publishes one expiring `orinoco-shacl-vue-input-<sha>` Actions artifact containing only `edit/config.json`, `edit/records.ttl`, and `edit/data/record-sources.json`.
A trusted default-branch `push` publishes the same three-file artifact for standalone editing at that exact default SHA.
These reproducible files are presentation input, not canonical metadata or a retained editor bundle.
Failure leaves the draft visibly blocked at the temporary handoff.
The service and workflow do not retain a second bundle, choose a disposition, add provenance or cache entries, approve, mark ready, merge, deploy, or write to a source.
The wrapper warns that a public Git host may retain the otherwise unreachable temporary object, so this path is only for data approved for public repository history.

[profile]: https://github.com/ORINOCO-Lite/orinoco-lite-dev/blob/main/docs/github-curation-review.md
[human-edit-profile]: https://github.com/ORINOCO-Lite/orinoco-lite-dev/blob/main/docs/github-shacl-vue-edit.md
