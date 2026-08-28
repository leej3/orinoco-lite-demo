# Retained-source adapters

This directory contains template-owned executable adapter support. A site
keeps each source's manifest, captured content, transformation evidence, and
policy under `site-specific/sources/<id>/`; compact human decisions belong
under `site-specific/curation-records/`.

Run the deterministic retained-source checks with:

```console
pixi run source-adapter-canary
```

The canary never fetches a source. Network-backed refresh and review remain
explicit operator actions through the metadata review wrapper.
