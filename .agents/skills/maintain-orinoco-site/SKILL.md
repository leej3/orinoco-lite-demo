---
name: maintain-orinoco-site
description: Inspect, validate, and review an ordinary released Orinoco Lite downstream while preserving site-owned data and extensions. Use for immutable-release review, downstream recovery, or a deliberate release-adoption pull request. Do not use to release the package or template, change external source systems, or change the package/template update implementation.
---

# Maintain an Orinoco site

Keep released scaffold maintenance separate from the site's data, policy, appearance choices, source configuration, curation decisions, and source-adapter extensions.

## Establish the local contract

1. Read applicable `AGENTS.md`, the site README, `.copier-answers.yml`, `pixi.toml`, `pixi.lock`, and the current Git diff.
2. Use `docs/ownership.md` to distinguish the scaffold from site-owned inputs and extensions.
3. Resolve package or template defects in their owning repositories.

## Update the template and package

Use **Actions → Update downstream template** for a GitHub-only update, or run `pixi run orinoco-lite template update --revision REVISION` in a clean local checkout.
The selected template supplies the default package revision; use `--package-revision` and `--package-repository` only for an explicit override.
The command records Copier's actual update through DataLad and records an override separately.

Review the draft pull request, site-owned inputs, submodule selections, and validation results.
Conflicts are committed for browser editing; resolve them in a separate commit before merging.
Follow `docs/template-updates.md` for conflict resolution, historical replay, and rollback through Git revert.
Start a fresh Pixi invocation after changing the package selection.
Do not replace the command with a hand-maintained file-copy list.

## Validate and hand off

- Run an extension's own focused test when its behavior changes, followed by `pixi run orinoco-lite validate` and `pixi run build`.
- Run the relevant browser acceptance for changed routes.
  For release adoption, finish with `pixi run build && pixi run orinoco-lite verify-site build/site` and review the rendered result.
- When enabling hosted editing or changing the Pages hostname, follow `docs/custom-domain.md`: verify the custom domain in GitHub and Pages, update `site.base_url`, and confirm the deployed `/edit/` flow no longer shows the shared-`github.io` warning.
  **Download bundle** remains available either way.
- Review locks, site-owned files, conflicts, and the final diff before using the downstream's normal pull-request, merge, and deployment policy.
  Do not infer approval, merge, release, or deployment authority.

Use `$manage-orinoco-content` for editorial content and declared assets.
Use `$operate-orinoco-metadata-adapters` for source capture, candidate review, provenance, and durable human curation decisions.
