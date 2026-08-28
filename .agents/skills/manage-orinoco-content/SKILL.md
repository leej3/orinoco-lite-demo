---
name: manage-orinoco-content
description: Edit and review an Orinoco Lite downstream's human-authored editorial content and declared assets. Use when changing files under site-specific/content/pages or site-specific/static, updating asset declarations, reviewing focused editorial or asset diffs, or preparing an ordinary site content pull request. Do not use for metadata source adapters or human curation decisions.
---

# Manage Orinoco content

Work only in the downstream's user-facing source layer.
Keep generated output, tool state, and migration evidence out of content commits.

## Editorial workflow

1. Read `site-specific/site.yaml` and `site-specific/projection.yaml` to
   understand which routes are structured and which need bespoke editorial
   files.
2. Edit Markdown under `site-specific/content/pages/`; preserve existing front matter and navigation intent.
3. Do not edit `generated/`.
Run `pixi run validate` to regenerate it locally.
4. Run `pixi run build` and inspect the affected page before committing.
5. Keep the commit focused on source files; ignored projection output is not review evidence.

## Asset workflow

1. Put site-managed payloads under `site-specific/static/files/`.
2. Update `site-specific/static/manifest.yaml` when an asset is part of the declared build contract.
3. For committed payloads, use ordinary Git.
Do not initialize git-annex, add annex pointer rules, or introduce a large-file backend.
4. For payloads fetched from an external source, record only the immutable URL, byte size, and SHA-256 needed to verify that external fact.
Do not duplicate Git blob or commit identity in a separate inventory.
5. Run `pixi run assets-verify`, `pixi run validate`, and `pixi run build`.

## Boundaries

- Treat `site-specific/` and `extensions/` as user-facing source.
- Keep an executable presentation override required only by this site under
  `extensions/site/layouts/`; propose reusable layout behavior to the framework.
- Treat `.orinoco-lite/site/` and `.orinoco-lite/source-adapters/` as
  template-owned implementation support.
Change it only for an explicit framework-maintenance task.
- Never commit `generated/`, `.orinoco-lite/state/`, caches, build output, or a second digest inventory of the same commit.
- Prefer a small source diff plus rendered review over provenance narration in the downstream tree.

## Metadata-adapter handoff

Use `$operate-orinoco-metadata-adapters` for files under
`site-specific/sources/` or `site-specific/curation-records/`, candidate
metadata, provenance, matching policy, decision memory, or adapter pull
requests. That workflow distinguishes captured evidence from durable human
curation state and requires explicit review decisions.
