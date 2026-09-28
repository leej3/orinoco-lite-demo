# Hugo asset notices

Maintainers copy the Hugo assets required by downstream builds into `materialized-hugo-assets/upstream/` at their original upstream-relative paths.
The adjacent `materialized-hugo-assets/LICENSE` applies to that bounded ordinary-file overlay.
Applicable notices are preserved in the overlay when it is populated.

The complete reusable website, its projection templates, and its theme are not copied into this scaffold.
The selected Orinoco Lite package resolves them at build time and preserves notices supplied by their dependency closure, including Congo's MIT notice.
