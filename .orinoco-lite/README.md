# Orinoco support files

This tracked directory contains the implementation support behind the small root-level downstream interface.

- `tools/` contains helpers invoked by Pixi tasks and workflows.
- `site/` contains the template-managed static-site framework, structured-data
  render templates, and projection implementation.
- `source-adapters/` contains template-managed generic adapter executables.
- `tests/` contains site behavior, source-adapter, and offline checks.

Generated projection and update state are ignored under `generated/` and `.orinoco-lite/state/`.
Downloads, installed runtimes, and caches are ignored under `.orinoco/`, `.pixi/`, and `build/`.
User-facing metadata, editorial content, assets, structured site data, source
manifests/evidence/policy, and decisions remain under `site-specific/`.
