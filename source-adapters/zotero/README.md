# Public Zotero source adapter

This directory retains the complete reviewed Milestone 3 Zotero evidence from `dump-research-info` commit `062da59cb5a00ca128b3df895426a54088bfc625` and implements the site-owned Milestone 5 candidate provider.

- `source/` contains the reviewed public API snapshot and deterministic transformed class files;
- `policy/` contains the reviewed creator, addition, migration, and site policies;
- `tools/` contains the read-only acquisition and transformation tools adapted to this ordinary downstream repository; and
- `candidates.py` derives an ephemeral shared `CandidatePlan` from the exact
  source revision, canonical metadata base, and the trusted host's reviewed
  adapter provenance identity.
The trusted host also supplies a `SchemaView` constructed from the pinned Things Schema in the verified released runtime.
For proposal and finalization, it passes the trusted default checkout separately as `trusted_root`.
Executable helpers, the frozen snapshot and transformed candidates, and site policy are read only from that trusted checkout.
The proposal-parent `root` supplies only canonical records, annotation companions, and the compact decision cache; omitting `trusted_root` retains same-root local development behavior.

Candidate construction uses the pinned upstream ownership helpers through the shared Orinoco Lite enrichment bridge.
Canonical records retain topical fields and actual qualified `AttributeSpecification` or `Statement` objects.
Mirrored annotation companions contain only the PAV removed from those objects.
An existing topical field is never replaced by a source value; when the topical field is absent but an equivalent unowned assertion exists, the upstream convenience copy is proposed without new PAV.

The compact decision fingerprint covers the adapter-owned, unannotated semantic proposal fragment, including its qualified assertions and policy-created output.
PAV, transport coordinates, formatting, unused source facts, curated baseline content, and an adapter-version change by itself do not change that fingerprint.

An unchanged equivalent object remains unowned and does not receive a provenance-only rewrite.
When a mapped source field is absent, the upstream helper removes obsolete qualified assertions and PAV owned by this adapter.
It preserves top-level curated values plus human and differently owned assertions, and it does not manufacture an empty topical field.
Source absence does not propose deletion of an entire record because this adapter has no reviewed record-deletion trigger.
The current decision state, when it exists, is the compact shared cache at `policy/curation-decisions.yaml`.

The candidate provider writes only ignored transformation output.
The trusted proposal task applies the reviewable plan through the project DataLad boundary; neither path writes to Zotero.
A production proposal also remains intentionally blocked until the separate
corpus-normalization change has landed and the configured provenance identity
identifies an existing reviewed `xyzri:XYZInstrument` record.
The trusted `sources.toml` maps Zotero adapter version 1 to
`xyzrins:source-adapters/zotero/v1`; operators do not supply an identity or
fall back to another adapter version.
