# Getting started

1. Set the site identity and canonical public URL in `site-specific/site.yaml`.
2. Replace the starter records and `/explore` page with reviewed site metadata and editorial content before publishing.
3. Add further editorial pages, assets, and static inputs only under their `site-specific/` directories.
4. Run `pixi run build`; it validates the inputs as part of building the site.
5. Configure repository Pages and curation settings before enabling hosted editing.

Orinoco Lite supplies the default projection and resolves the www-from-model checkout selected by its packaged resources.
Ordinary site construction should use declarative inputs and the supported small overrides under `site-specific/overrides/`, not copy the upstream Hugo components into this repository.

Use `pixi run orinoco-lite validate` to check inputs without generating a site or projection, and `pixi run orinoco-lite verify-site build/site` to check an existing local build without rebuilding it.
Builds reuse unchanged metadata projections; `pixi run build --no-cache` repeats projection and semantic checks without fetching new source data.

For GitHub Pages, `pixi run build-pages` builds the website and emits its publication bundle from clean committed inputs.
The workflow deploys the site, then runs `publication record` to retain that successful build outside the source branch.
There is no separate preparation step and no rebuild after deployment.

The template's required materialized Hugo assets are ordinary files under `.orinoco-lite/materialized-hugo-assets/upstream/`.
Site-specific assets belong under `site-specific/`; downstream tasks never hydrate either tree with Git Annex.

Metadata acquisition and curation programs may live under `extensions/` and run through explicit adapter tasks.
They must write proposals or reviewed metadata inputs; the website build never imports or executes them.
