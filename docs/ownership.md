# File ownership

`.orinoco-lite/template-ownership.yml` is the executable ownership contract.

| Class | Who changes it | Examples |
| --- | --- | --- |
| `template_owned` | Copier, with three-way conflict handling | generic workflows, command facade, updater, verifier, generic adapters, and generic docs |
| `initialized_site_owned` | The site after one-time creation | `orinoco.yaml`, metadata, the concrete curation workflow, `custom/`, `site/`, and source adapters |
| `engine_lock` | The pinned updater, as a reviewed structured diff | `orinoco.lock` and `pixi.lock` |
| `extensions` | The site | stable custom behavior under `extensions/` |
| `consumer_tests` | The site after one-time creation | browser, source-adapter, and offline behavior tests |
| `site_policy` | The site | license, citation, contribution, and conduct files |
| `generated` | Ignored runtime output | projection under `generated/` |

If a downstream edit to a template-owned path overlaps an update, Copier writes a `.rej` conflict and the update stops for human review.
Site-specific operating guidance belongs in `site/README.md`, not in this template-owned document.

Copier creates initialized and test paths once, then excludes them from later overwrites.
The updater compares protected site-owned bytes before and after its run.
Generated projection and detailed updater state are ignored on the source branch; Git records the reviewable framework and source changes there.
After Pages deploys successfully, the workflow force-updates `latest-hugo-projection` with the complete projection commit and `gh-pages` with its generated-site child commit.

Source adapters use `source-adapters/`; their site-owned tests use `.orinoco-lite/tests/source-adapters/`.
The concrete `.github/workflows/curation-review.yml` follows that site-owned adapter policy.
The generic `.github/workflows/shacl-vue-proposal.yml` and its handoff helper
are template-owned because they operate only on the fixed-path review bundle,
shared canonical metadata, and exact Git coordinates. The site's static
`/edit/` route remains the sole editor; the configured curation service is only
an authenticated submission boundary.

Both `metadata/records/` and mirrored `metadata/overlays/annotations/` companions are protected site-owned semantic metadata.
A framework update may change support for joining them, but never their bytes.

`orinoco.lock` is the readable release authority.
Its diff carries exact engine, runtime, template, and workflow changes.
The matching `pixi.toml` wheel URL includes the reviewed SHA-256, and the frozen `pixi.lock` must resolve the same URL and version.
Ownership verification checks those pins together and, when the engine is installed, checks its distribution version.

Semantic content changes are never implicit.
A framework update that genuinely requires one must name a migration and list exact allowed site-owned paths.
The ledger records the changed hashes and remains in `human-review` status.

The site owns every tracked byte below `.orinoco-lite/tests/browser/`, including the npm manifest and lock.
The template owns only the installer facade; it must leave those tracked inputs unchanged.
See the checked browser README for the site-owned acceptance surface.

The template contract is maintained in the [template repository](https://github.com/ORINOCO-Lite/orinoco-lite-template).
Command semantics belong to the [engine repository](https://github.com/ORINOCO-Lite/orinoco-lite-dev).
