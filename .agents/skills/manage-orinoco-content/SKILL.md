---
name: manage-orinoco-content
description: Edit and review an Orinoco Lite downstream's human-authored editorial content and declared presentation inputs. Use when changing files under site-specific/content, site-specific/assets, site-specific/static, or supported overrides, reviewing a focused content diff, or preparing an ordinary site-content pull request. Do not use for metadata source adapters or human curation decisions.
---

# Manage Orinoco content

Work only in the downstream's user-facing source layer.
Keep generated output, tool state, and migration evidence out of content commits.

## Editorial workflow

1. Read `site-specific/site.yaml` and the existing files under
   `site-specific/content/` to understand the site's identity, navigation, and
   editorial conventions.
2. Edit Markdown under `site-specific/content/`; preserve existing front matter
   and navigation intent.
3. Do not edit `generated/`.
Run `pixi run validate` to regenerate it locally.
4. Run `pixi run build` and inspect the affected page before committing.
5. Keep the commit focused on source files; ignored projection output is not review evidence.

## Asset workflow

1. Put pipeline-consumed assets under `site-specific/assets/` and files copied
   directly into the site under `site-specific/static/`.
2. For committed payloads, use ordinary Git.
Do not initialize git-annex, add annex pointer rules, or introduce a large-file backend.
3. For payloads fetched from an external source, record only the immutable URL,
   byte size, and SHA-256 needed to verify that external fact.
Do not duplicate Git blob or commit identity in a separate inventory.
4. Run `pixi run validate` and `pixi run build`.

## Boundaries

- Treat `site-specific/` and `extensions/` as user-facing source.
- Keep a site-specific layout, configuration, or static override under the
  matching `site-specific/overrides/` directory. Propose reusable presentation
  behavior to the template or pinned upstream.
- Treat `.orinoco-lite/presentation/` and
  `.orinoco-lite/materialized-presentation/` as template-owned presentation
  inputs. Change them only for an explicit template-development or maintainer
  repinning task.
- Keep executable metadata acquisition and curation code under
  `extensions/source-adapters/`; it is never website presentation code.
- Never commit `generated/`, `.orinoco-lite/state/`, caches, build output, or a
  second digest inventory of the same commit.
- Prefer a small source diff plus rendered review over provenance narration in the downstream tree.

## Metadata-adapter handoff

Use `$operate-orinoco-metadata-adapters` for files under
`extensions/source-adapters/`, `site-specific/sources/`, or
`site-specific/curation-records/`, candidate metadata, provenance, matching
policy, decision memory, or adapter pull requests. That workflow distinguishes
captured evidence from durable human curation state and requires explicit
review decisions.
