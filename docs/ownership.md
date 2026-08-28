# File ownership

`.orinoco-lite/template-ownership.yml` is the executable ownership contract.

| Class | Who changes it | Examples |
| --- | --- | --- |
| `template_owned` | Copier, with three-way conflict handling | generic workflows, command facade, `.orinoco-lite/site/`, `.orinoco-lite/source-adapters/`, updater, verifier, and generic docs |
| `initialized_site_owned` | The site after one-time creation | `orinoco.yaml`, `site-specific/`, and the concrete curation workflow |
| `engine_lock` | The pinned updater, as a reviewed structured diff | `orinoco.lock` and `pixi.lock` |
| `extensions` | The site | stable custom behavior under `extensions/` |
| `consumer_tests` | The site after one-time creation | browser, source-adapter, and offline behavior tests |
| `site_policy` | The site | license, citation, contribution, and conduct files |
| `generated` | Ignored runtime output | projection under `generated/` |

If a downstream edit to a template-owned path overlaps an update, Copier writes a `.rej` conflict and the update stops for human review.
Site-specific operating guidance belongs under `site-specific/`, not in this template-owned document.

Copier creates initialized and test paths once, then excludes them from later overwrites.
The updater compares protected site-owned bytes before and after its run.
Generated projection and detailed updater state are ignored on the source branch; Git records the reviewable framework and source changes there.
After Pages deploys successfully, the workflow force-updates `latest-hugo-projection` with the complete projection commit and `gh-pages` with its generated-site child commit.

Generic source-adapter executables use `.orinoco-lite/source-adapters/`.
Their manifests, captured content, evidence, mapping policy, and compact
decision records use `site-specific/sources/` and
`site-specific/curation-records/`; the template-owned fast canary uses
`.orinoco-lite/source-adapters/tests/`, while fuller site-owned tests use
`.orinoco-lite/tests/source-adapters/`.
The concrete `.github/workflows/curation-review.yml` follows that site-owned
source policy.
The generic `.github/workflows/shacl-vue-proposal.yml` and its handoff helper are template-owned because they operate only on the fixed-path review bundle, shared canonical metadata, and exact Git coordinates.
The site's static `/edit/` route remains the sole editor; the central curation service, or the one optional self-hosted override, is only an authenticated submission boundary.
The trusted Pages build derives repository identity from GitHub rather than a site-owned editor setting.
Source-adapter workflows remain site-owned and link to the site's own static `/review/` route; the selected service provides OAuth and verified GitHub transport, not another review page.
The custom-domain and shared-origin behavior is documented in [Custom domain and secure GitHub submission](custom-domain.md).

Both `site-specific/metadata/records/` and mirrored `site-specific/metadata/overlays/annotations/` companions are protected site-owned semantic metadata.
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
