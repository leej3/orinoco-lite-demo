# Orinoco Lite site

This is an Orinoco Lite metadata-driven website.
Set its public identity in `site-specific/site.yaml`.
The starter records and `/explore` page build and preview immediately; replace them with reviewed site metadata and editorial content before publishing.
Orinoco Lite resolves its pinned upstream presentation and composes it with this scaffold's small `.orinoco-lite/presentation/` adapter, its bounded `.orinoco-lite/materialized-presentation/upstream/` asset overlay, and the repository's declarative `site-specific/` inputs.

```console
pixi run build
pixi run serve
```

The source boundary is:

- `site-specific/metadata/` — semantic records and curation annotations;
- `site-specific/content/` — editorial Markdown;
- `site-specific/assets/` and `site-specific/static/` — declared website data;
- `site-specific/site.yaml` — identity, navigation, and supported presentation choices;
- `site-specific/overrides/` — explicit declarative config, layout, or static overrides; and
- `extensions/` — optional metadata acquisition and curation executables that never ship with or execute during the website build.

See [getting started](docs/getting-started.md), [ownership](docs/ownership.md), and [custom-domain setup](docs/custom-domain.md). Pull requests also get a disposable Netlify rendering; see [pull-request previews](docs/pr-previews.md).

The selected package revision is the single authority for the upstream website and theme pins.
The downstream selects its package through Pixi, its template through `.copier-answers.yml`, and actions through pinned workflow references.
Resources and specifications required to build or operate Orinoco Lite are internal to the selected package commit.
