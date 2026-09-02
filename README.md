# Orinoco Lite site

This is an Orinoco Lite metadata-driven website. Set its public identity in
`site-specific/site.yaml`. The engine resolves its pinned upstream presentation
and composes it with this scaffold's small `.orinoco-lite/presentation/`
adapter, its bounded `.orinoco-lite/materialized-presentation/upstream/` asset
overlay, and the repository's declarative `site-specific/` inputs.

```console
pixi run validate
pixi run build
pixi run serve
```

The source boundary is:

- `site-specific/metadata/` — semantic records and curation annotations;
- `site-specific/content/` — editorial Markdown;
- `site-specific/assets/` and `site-specific/static/` — declared website data;
- `site-specific/site.yaml` — identity, navigation, and supported presentation
  choices;
- `site-specific/overrides/` — explicit declarative config, layout, or static
  overrides; and
- `extensions/` — optional metadata acquisition and curation executables that
  never ship with or execute during the website build.

See [getting started](docs/getting-started.md),
[ownership](docs/ownership.md), and [custom-domain setup](docs/custom-domain.md).

The engine runtime is the single authority for the upstream website and theme
pins. The downstream selects its engine, runtime, template, and workflow
releases exactly in `orinoco.lock` and `.copier-answers.yml`.
