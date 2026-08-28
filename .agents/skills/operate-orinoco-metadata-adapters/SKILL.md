---
name: operate-orinoco-metadata-adapters
description: Run template-owned Orinoco Lite metadata adapter executables against site-owned source manifests and evidence with explicit human decisions. Use in a downstream or template repository when capturing source snapshots, generating candidate changes, reviewing accept/reject/link/defer/supersede choices, updating durable decision memory, applying decisions, opening adapter pull requests, checking provenance, or determining whether local locks or newer upstream behavior governs. Do not use for ordinary editorial or asset work.
---

# Operate Orinoco metadata adapters

Treat an adapter run as a human-reviewed curation workflow. A clean command,
valid record, or plausible match is evidence, not permission to change accepted
metadata.

## Resolve the site's contract

1. Read every applicable `AGENTS.md`, the site README, ownership manifest,
   `orinoco.lock` or equivalent release evidence, the applicable
   `site-specific/sources/<id>/source.yaml`, adapter README, tests, and
   site-owned policy or decision registers.
2. Confirm whether the checkout is Copier source, a staged template render, a
   released downstream consumer, or a source-data checkout.
3. For ordinary site work, follow the consumer's locked release and the
   adapter's local contract. Remote latest is advisory until a reviewed framework
   update changes the lock.
4. If the task asks about new upstream behavior, verify the authoritative remote
   and compare it with the local pin. Label supported, current-upstream, and
   proposed behavior separately. Do not treat an issue or pull request as merged.
5. Check the human-review queue before generating or applying identity,
   eligibility, venue, topic, relationship, or other ambiguous semantics.

Do not assume this skill's workflow means the site already implements a generic
decision-file schema. Follow the adapter's tested local format. Escalate missing
shared machinery as an Orinoco Lite engineering change.

## Keep each state in its authority

- Store accepted Things and supported assertion provenance in the site's
  canonical metadata records.
- Store captured source content, evidence, and mapping policy under
  `site-specific/sources/<id>/`. Store durable human dispositions under
  `site-specific/curation-records/`. This is decision memory, not an evictable
  cache.
- Keep source-response caches, lookup indexes, temporary reports, browser output,
  generated projections, and builds ignored.
- Use Git or DataLad to record execution and content history. Do not expect a Git
  diff alone to explain why an absent record was rejected.
- Keep semantic mappings distinct from operational matches and review
  dispositions. If both Things and SSSOM forms exist, use the locally declared
  canonical authority and generate the other.

Read [adapter-review.md](references/adapter-review.md) in full before recording
decisions, applying a review bundle, or preparing an adapter pull request.

## Run the review transaction

1. Start from a clean, current review branch in the downstream repository.
2. Capture or select the declared source snapshot and run the exact adapter
   command from `.orinoco-lite/source-adapters/`. Keep literal
   project-relative arguments in the execution record. Run
   `pixi run source-adapter-canary` before a network-backed refresh.
3. Review candidate identities, assertions, provenance, collisions, unresolved
   items, and the effect of prior decisions. Do not auto-create or fuzzy-link an
   ambiguous entity.
4. Present each unresolved choice to the human as an atomic decision with
   evidence, consequences, and a recommendation clearly labeled as such.
5. Record only the human's explicit accept, reject, link, defer, or supersede
   choice, including the scope and rationale required by local policy.
6. Run the adapter's decision-application command. Attach the supported PAV or
   statement-level provenance to accepted assertions without fabricating human
   authorship for machine-generated content.
7. Run local validation, adapter tests, deterministic or idempotence checks, and
   any focused rendered review required by the site.
8. Re-read the final diff and ensure every candidate is handled or visibly
   pending. Validate again after the last decision change.

When every candidate is rejected, prepare and preserve a decision-only pull
request for human review and merge if local policy allows. Closing the proposal,
deleting an inbox item, or leaving no metadata diff does not record the decisions.

## Prepare human review

Keep source capture distinct from semantic curation when the adapter contract
requires separate changes. Otherwise prefer one review pull request that ends
with both explicit decision state and the metadata application, so the branch is
self-consistent.

In the pull-request summary, identify:

- base revision, source snapshot, adapter and policy versions, and literal run
  command;
- records or assertions proposed, accepted, linked, rejected, deferred, or
  superseded;
- stale decisions or materially changed proposals returned to review;
- ambiguous or unresolved items and the human choices still required;
- metadata, provenance, mapping, rendered-site, and idempotence checks run;
- whether the diff is decision-only.

A bot may copy an explicit human choice into the review branch or open a
decision-only fallback pull request. It must not infer a choice from comments,
labels, silence, closure, or absence; approve; merge; or deploy. Require human
review of the latest pushed head.

Use `$manage-orinoco-content` for ordinary editorial and asset work. Use
`$develop-orinoco-lite`, when available in the engineering workspace, for engine,
schema, shared adapter-contract, or upstream-adoption changes.
