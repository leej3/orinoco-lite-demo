# Orinoco Lite site

This is an Orinoco Lite metadata-driven website.
Set its public identity in `site-specific/site.yaml`.
The package resolves its pinned upstream presentation and composes it with this scaffold's small `.orinoco-lite/presentation/` adapter, its bounded `.orinoco-lite/materialized-presentation/upstream/` asset overlay, and the repository's declarative `site-specific/` inputs.

```console
pixi run validate
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

See [getting started](docs/getting-started.md), [ownership](docs/ownership.md), and [custom-domain setup](docs/custom-domain.md).

The released package is the single authority for the upstream website and theme pins.
The downstream selects its package release in the `package` mapping in `orinoco.lock` and the `package_version`, `package_url`, and `package_sha256` answers in `.copier-answers.yml`.
Template and workflow selections remain separate coordinates.
