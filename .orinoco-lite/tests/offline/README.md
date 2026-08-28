# Warmed-cache offline acceptance gate

The complete cold-offline site is not a Milestone 4 promise. This consumer
intentionally keeps sixteen large, digest-addressed annex payloads as
read-only hydration contracts instead of committing their bytes.

Before network denial, the final regression must install dependencies and the
runtime, then run `pixi run assets-hydrate`. That command hydrates all sixteen
payloads into the engine's asset cache and verifies each cached size and
SHA-256 digest against `site-specific/static/manifest.yaml`. This is an explicit setup
phase,
not part of the offline claim.

With that verified cache in place, the regression must deny all network access
and first run `pixi run assets-verify`, then replay validation, projection
verification and update, both deterministic site builds, and the editor
contract. Those repeated commands may read the verified cache but may not make
any network call or fetch a schema, template, editor resource, or asset.

On macOS, wrap each command with:

```text
sandbox-exec -f .orinoco-lite/tests/offline/macos-network-deny.sb <command>
```

The profile denies every network operation at the operating-system boundary.
The release's Linux gate runs the same commands in an environment with no
network namespace. Proxy-only simulation is not accepted as offline evidence.

This proves warmed-cache offline operation. Materializing the sixteen annex
payloads in the repository, and complete cold-offline operation, remain
deferred by the accepted M4-I002 asset policy.
