---
name: maintain-orinoco-site
description: Inspect, validate, and review an ordinary released Orinoco Lite downstream while preserving site-owned data and extensions. Use for immutable-release review, downstream recovery, or a deliberate release-adoption pull request. Do not use to release the package or template, change external source systems, or invent an in-place template updater.
---

# Maintain an Orinoco site

Keep released scaffold maintenance separate from the site's data, policy, presentation choices, source configuration, curation decisions, and source-adapter extensions.

## Establish the local contract

1. Read every applicable `AGENTS.md`, the site README, `.orinoco-lite/template-ownership.yml`, `.copier-answers.yml`, `orinoco.lock`, and the current Git diff.
2. Use the ownership manifest to distinguish template-owned files, exact package and workflow pins, create-once site files, extensions, and optional downstream tests.
Do not infer ownership from a path convention that the site has not adopted.
3. Treat remote latest versions as advisory.
Adopt only reviewed, immutable tags, URLs, digests, and workflow commits.

## Adopt a reviewed release

1. Start with a clean worktree and the exact reviewed package and template release proposed for this downstream.
2. Review the release's downstream pull request as one change.
The package, template, and workflow coordinates must remain a coherent reviewed set; this scaffold intentionally provides no in-place updater.
3. Confirm that declarative `site-specific/` inputs, `extensions/`, downstream acceptance tests, and repository policy change only when the pull request explicitly requires and explains that site-owned change.
4. Put reusable defects in the package or template.
Keep site-specific behavior in declared downstream inputs, supported overrides, or metadata-adapter extensions.

## Validate and hand off

- Run an extension's own focused test when its behavior changes, followed by `pixi run validate` and `pixi run build`.
- Run the relevant browser acceptance for changed routes.
For release adoption, finish with `pixi run verify-build`, run any site-owned acceptance tests, and review the rendered result.
- When enabling hosted editing or changing the Pages hostname, follow `docs/custom-domain.md`: verify the custom domain in GitHub and Pages, update `identity.base_url` in `site-specific/site.yaml`, and confirm the deployed `/edit/` flow no longer shows the shared-`github.io` warning.
**Download bundle** remains available either way.
- Review locks, site-owned files, conflicts, and the final diff before using the downstream's normal pull-request, merge, and deployment policy.
Do not infer approval, merge, release, or deployment authority.

Use `$manage-orinoco-content` for editorial content and declared assets.
Use `$operate-orinoco-metadata-adapters` for source capture, candidate review, provenance, and durable human curation decisions.
