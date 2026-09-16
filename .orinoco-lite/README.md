# Orinoco Lite template internals

`.orinoco-lite/presentation/` is a small adapter applied to the upstream presentation resolved by the selected Orinoco Lite package.
It contains only the configuration, footer, and static-file templates needed to map the generic source to this downstream contract; it is not a standalone website.

`.orinoco-lite/materialized-presentation/upstream/` is the bounded overlay for required presentation assets copied by maintainer repinning.
Files retain their upstream-relative paths and are ordinary Git files covered by the adjacent `LICENSE`; the downstream never uses Git Annex.

Executable commands are supplied by the installed `orinoco-lite` package and invoked through Pixi tasks.
