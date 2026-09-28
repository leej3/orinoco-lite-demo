# Orinoco Lite template internals

`.orinoco-lite/hugo-adapter/` is a small adapter applied to the www-from-model checkout resolved by the selected Orinoco Lite package.
It contains only the configuration, footer, and static-file templates needed to map the generic source to this downstream contract; it is not a standalone website.

`.orinoco-lite/materialized-hugo-assets/upstream/` is the bounded overlay for required Hugo assets copied by maintainer repinning.
Files retain their upstream-relative paths and are ordinary Git files covered by the adjacent `LICENSE`; the downstream never uses Git Annex.

Executable commands are supplied by the installed `orinoco-lite` package and invoked through Pixi tasks.
