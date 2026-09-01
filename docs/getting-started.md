# Getting started

1. Set the site identity and canonical public URL in
   `site-specific/site.yaml`.
2. Add reviewed site metadata under `site-specific/metadata/records/`.
3. Add editorial pages, assets, and static inputs only under their
   `site-specific/` directories.
4. Run `pixi run validate`, `pixi run build`, and `pixi run test-all`.
5. Configure repository Pages and curation settings before enabling hosted
   editing.

The engine supplies the default projection and resolves the presentation
selected by its verified runtime manifest. Ordinary site construction should
use declarative inputs and the supported small overrides under
`site-specific/overrides/`, not copy the upstream presentation into this
repository.

The template's required materialized presentation assets are ordinary files
under `.orinoco-lite/materialized-presentation/upstream/`. Site-specific assets
belong under `site-specific/`; downstream tasks never hydrate either tree with
Git Annex.

Metadata acquisition and curation programs may live under `extensions/` and
run through explicit adapter tasks. They must write proposals or reviewed
metadata inputs; the website build never imports or executes them.
