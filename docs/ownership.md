# File ownership

The template owns the downstream scaffold, generic workflows and documentation, and every path under `.orinoco-lite/`.
That private namespace contains the small presentation adaptation, bounded licensed asset overlay, and tools that must remain aligned with the selected package and template.
The package resolves the complete upstream website, projection templates, and theme rather than copying them into this repository.

The downstream owns all declarative inputs under `site-specific/`, executable metadata adapters under `extensions/`, optional downstream-specific tests under `tests/`, release selection, repository policy, and generated deployment history.

Ordinary presentation belongs in `site-specific/site.yaml`, content, assets, and static inputs.
A custom layout is supported only as an explicit file under `site-specific/overrides/layouts/`.
Website code under `extensions/` is invalid, and extension source or generated outputs are never copied into a build.
