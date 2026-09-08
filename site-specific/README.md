# CON ORINOCO site-specific content

The full provenance for the content of this repository ATM is vague, and
spread around 

- https://github.com/con/dump-research-info/
- https://github.com/leej3/orinoco-lite-demo 
- https://centerforopenneuroscience.org

The "history" of trimage of orinoco-lite-demo could be found in
https://claude.ai/share/a2aeb683-48a5-4f41-aff1-db2287ef3566 .

## Structure

| Path | Purpose |
| --- | --- |
| `metadata/records/` | Every Things YAML record supplied to projection; preserve stable PIDs. |
| `site.yaml` | CON identity, language, author, theme, navigation, people, project, and webmanifest data. |
| `content/pages/` | Human-authored pages and navigation content. |
| `static/` | Site-owned payloads and the digest/retrieval contract in `site-specific/static/manifest.yaml`. |
| `sources/`, `site-specific/curation-records/` | Read-only source evidence, site policy, and compact reviewed decisions. |

The template owns a small framework facade around those paths: workflows, commands, updater and ownership tools, and generic contract documentation.
The executable ownership contract at `.orinoco-lite/template-ownership.yml` is authoritative when the prose and a path disagree.
The updater protects site-owned bytes and stops for review rather than silently reconciling them.

## Edit, validate, and preview

The target for use: ATM this is all done when this repository tree is
embedded (via submodule or subtree) into the orinoco-lite-template based
instance.
