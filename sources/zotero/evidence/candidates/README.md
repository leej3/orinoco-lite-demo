# Center for Open Neuroscience Zotero library

## Source

- Library: https://www.zotero.org/groups/6197458/centerforopenneuroscience/library
- Zotero group ID: `6197458`
- API root: https://api.zotero.org/groups/6197458
- Access: public, read-only ingestion

This directory contains validated JSON arrays generated from the CON Zotero group.
It is refreshed only through the snapshot, candidate, review, and promotion boundaries described below.

## Inclusion policy

Include items assigned to these collections:

- `CON Articles & Posters` (historically `CON Articles`)
- `CON Datasets`
- `CON Zenodo/OSF DOIs`
- `CON Software`

The importer also accepts the historical article label and the historical forms without the `CON` prefix so that a collection-label migration does not silently reclassify records.

Exclude `External`.
Treat unfiled items as review candidates rather than automatic additions.
Attachments and notes are supporting source material, not independent research-information records unless a reviewer promotes one as a first-class document.

## Current inventory

The public API snapshot fetched at `2026-08-26T20:59:27Z` records library version `668` and normalized payload digest `23aa443a248e9e1dfc73003cde76f3a93c533bf9e57cc5674539b80da52f17b8`:

- 190 top-level items;
- 126 memberships in `CON Articles & Posters`;
- 55 in excluded `External`;
- 4 in `CON Datasets`;
- 3 in `CON Zenodo/OSF DOIs`;
- 0 in `CON Software`; and
- 3 unfiled top-level items.

The current snapshot contains no normalized DOI collision groups.
Zotero consolidated seven duplicate top-level items and records each retired item in the surviving item's `dc:replaces` relation.
The six affected publication candidates therefore drop only identifiers that belonged to retired items; their titles, types, dates, creators, and stable DOI identities are unchanged.

## Target mapping

| Zotero information | Target |
| --- | --- |
| Journal article, conference paper, preprint, book section, report, thesis | `XYZPublication.json` |
| Dataset or registry-classified dataset | `XYZDataset.json` |
| Computer program or registry-classified software | `XYZInstrument.json`, kind `obo:IAO_0000010` |
| Genuinely generic document | `XYZDocument.json` |
| Publication venue referenced by accepted publications | `XYZPublicationVenue.json` |
| Creators not already represented | `XYZPerson.json` or `XYZOrganization.json` after reconciliation |

Generic Zotero `document` items require enrichment from Crossref or DataCite and collection context before class assignment.

## Refresh contract

The adapter:

1. fetch all collection and top-level item pages at one verified library version, with requested and returned API versions recorded;
2. exclude deleted, child, and `External` records from automatic publication;
3. normalize DOI, ISBN, PMID, PMCID, ORCID, and URL identifiers;
4. queues records that still require Crossref or DataCite enrichment rather than guessing a class;
5. reconcile against existing records by canonical identifier;
6. render stable, sorted candidate arrays;
7. validate candidates against the configured research-information collection;
8. present additions, changes, removals, collisions, and uncertain mappings for human review; and
9. updates this directory only through an explicit promotion command.

Acquisition and transformation run through checked-in Pixi tasks.
Zotero item keys and the library version must remain available as source identifiers/provenance even when a DOI becomes the entity `pid`.

## Current implementation

Fetch a live source snapshot and review its candidates without changing tracked evidence with:

```console
./.orinoco-lite/source-adapters/metadata/metadata-review review -- --only zotero
```

Candidate `XYZ*.json` files and the reconciliation report are written below `build/metadata-review/zotero/`.
After resolving every reported blocker, refresh only the committed snapshot and deterministic candidates with:

```console
./.orinoco-lite/source-adapters/metadata/metadata-review refresh-evidence -- --only zotero
```

Neither command promotes canonical metadata.

## Validated refresh

The version `668` snapshot deterministically generates 153 records, all of which pass the configured research-information validator:

- 4 `XYZDataset` records;
- 3 `XYZInstrument` records;
- 126 `XYZPublication` records; and
- 20 `XYZPublicationVenue` records.

All 126 publications and 20 venues reconcile to existing canonical records.
These are the same entities, not new IDs.
Multi-source consumers reconcile records by PID and apply the adapter's explicit field-level merge behavior.
The reviewed site policy includes 2 Brock Wester and 19 Russell Poldrack author attributions because both targets now have accepted canonical public person records.

The refresh report also identifies 1,817 unresolved creator occurrences across 1,221 names, 52 unmapped tag occurrences across 37 values, and 42 venue occurrences across 32 names without ISSNs.
Their source information remains in the committed Zotero snapshot for later registry enrichment and review; it is not converted into unsafe local identities.
