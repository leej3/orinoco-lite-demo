---
name: maintain-orinoco-site
description: Inspect, update, validate, and review an ordinary Orinoco Lite downstream while preserving site-owned data and extensions. Use for framework updates, immutable-pin review, compatibility maintenance, or downstream recovery. Do not use to release the engine or template, or to change external source systems.
---

# Maintain an Orinoco site

Keep framework maintenance separate from the site's data, policy, presentation,
source configuration, curation decisions, and source-adapter extensions.

## Establish the local contract

1. Read every applicable `AGENTS.md`, the site README,
   `.orinoco-lite/template-ownership.yml`, `.copier-answers.yml`, `orinoco.lock`,
   `docs/updating.md`, and the current Git diff.
2. Use the ownership manifest to distinguish template-owned files, exact engine
   and workflow pins, create-once site files, extensions, and consumer tests.
   Do not infer ownership from a path convention that the site has not adopted.
3. Treat remote latest versions as advisory. Adopt only reviewed, immutable
   tags, URLs, digests, and workflow commits.

## Apply a framework update

1. Start with a clean worktree and run `pixi run update-check`.
2. Review the proposed coordinates and compatibility notes, then apply them
   together with `pixi run update-orinoco -- ...` as documented locally.
   Do not hand-edit the template, engine, runtime, and workflow pins as
   independent upgrades.
3. Confirm that site-owned files are byte-identical. Stop for explicit review
   if Copier reports a conflict or the update needs a semantic migration.
4. Put reusable defects in the engine or template. Keep site-specific behavior
   in the downstream's declared site-owned paths or extension hooks.

## Validate and hand off

- Run the focused source-adapter or consumer test when its owned behavior
  changes, followed by `pixi run validate` and `pixi run build`.
- Run the relevant browser acceptance for changed routes. For a framework
  update, finish with `pixi run test-all` and review the rendered result.
- When enabling hosted editing or changing the Pages hostname, follow
  `docs/custom-domain.md`: verify the custom domain in GitHub and Pages, update
  `site.base_url`, and confirm the deployed `/edit/` flow no longer shows the
  shared-`github.io` warning. **Download bundle** remains available either way.
- Review locks, protected-file evidence, conflicts, and the final diff before
  using the downstream's normal pull-request, merge, and deployment policy.
  Do not infer approval, merge, release, or deployment authority.

Use `$manage-orinoco-content` for editorial content and declared assets. Use
`$operate-orinoco-metadata-adapters` for source capture, candidate review,
provenance, and durable human curation decisions.
