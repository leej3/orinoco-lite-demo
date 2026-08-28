# Zotero adapter executable

This is the standard read-only Zotero executable used by the generic metadata
review and curation runners. The site owns its library coordinate, collection
selection, identity mappings, captured snapshot, evidence, policies, and
decisions under `site-specific/sources/zotero/` and
`site-specific/curation-records/zotero.yaml`.

The source manifest supplies `group_id`, `included_collections`, and
`document_collection_classes`. The latter maps normalized collection names to
`dataset`, `instrument`, `publication`, or `registry`; no collection or site
identity is embedded in this executable.

The curation host reads executable code, captured source evidence, policy, and
the configured `provenance_identity` from the trusted checkout. A proposal
parent supplies only the configured canonical records, annotation companions,
and compact decision cache. Local development may use one checkout for both
roles.

Candidate construction preserves curated topical values and any human- or
differently owned assertions. Equivalent unowned assertions do not gain an
adapter provenance rewrite. If a mapped source field disappears, the adapter
may remove only assertions and provenance that it owns; source absence never
deletes an entire record.

Decision fingerprints cover the adapter-owned semantic proposal. PAV,
transport coordinates, formatting, unused source facts, curated baseline
content, and an adapter-version change alone do not alter that fingerprint.
The adapter writes only ignored transformation output and never writes to
Zotero. Production enablement requires the configured provenance identity to
resolve to a reviewed instrument record.
