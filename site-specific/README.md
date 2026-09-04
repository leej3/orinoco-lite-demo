# Operating the CON Orinoco test site

This guide covers the site-owned layer of `orinoco-lite-demo`.
It is for reviewing and operating this concrete CON test site.
The repository carries the accepted CON metadata baseline, reviewed source-adapter records and provenance, editorial pages, presentation choices, declared assets, and supported presentation overrides.

This repository is a distribution fixture, not a production-content approval.
Reuse is encouraged under the matrix in [`LICENSES.md`](../LICENSES.md): original software is MIT, original prose is CC BY 4.0, and factual metadata is CC0 1.0.
Media and third-party material remain subject to their item-specific licenses and notices.

## Ownership boundaries

| Path | Responsibility |
| --- | --- |
| `site-specific/metadata/records/` | Reviewed Things YAML records; preserve stable PIDs. |
| `site-specific/metadata/overlays/annotations/` | Machine-managed provenance companions. |
| `site-specific/site.yaml` | CON identity, language, navigation, and presentation choices. |
| `site-specific/content/pages/` | Human-authored editorial pages. |
| `site-specific/assets/`, `site-specific/static/` | Declared site assets and static payloads. |
| `site-specific/overrides/` | Explicit presentation overrides. |
| `site-specific/sources/`, `site-specific/curation-records/` | Source evidence, site policy, and compact reviewed decisions. |
| `extensions/source-adapters/` | Site-owned metadata acquisition and curation executables. |
| `.orinoco-lite/` | Template-owned presentation adaptation, licensed assets, and helper tools. |
| `generated/`, `build/` | Ignored projection and website output. |
| `orinoco.lock` | Exact package, template, and reusable-workflow release selection. |

The template supplies workflows, commands, ownership tools, and generic documentation.
The executable ownership contract at `.orinoco-lite/template-ownership.yml` defines the supported boundaries.

## Edit, validate, and preview

For a metadata, editorial, asset, or presentation change:

```console
pixi run validate
pixi run projection-verify
pixi run build
pixi run serve
```

The tasks regenerate ignored projection output from reviewed source records.
Review the source diff and the rendered build; do not commit or hand-edit projection output.
Before proposing a change, run `pixi run verify-ownership`, `pixi run verify-hugo`, and `pixi run verify-build`.
Run an adapter's focused tests when changing its executable behavior.

Assets used through Hugo's asset pipeline belong in `site-specific/assets/`; files published verbatim belong in `site-specific/static/`.
The template's required presentation assets are ordinary files under `.orinoco-lite/materialized-presentation/upstream/`.
Downstream builds do not require Git Annex.
Source-adapter tasks use DataLad to record run provenance in Git.

## Local and Pages URL behavior

`pixi run build` creates `build/site` with `/` as its base URL.
`pixi run serve` serves those files on port 8765 through `http://127.0.0.1:8765/` or `http://localhost:8765/`.
The Pages workflow builds separately with the canonical public base URL from `site-specific/site.yaml`, including its project path.
Do not hard-code a loopback origin, Pages hostname, or production domain into editorial content.

## Browser editing and source review

The static `/edit/` interface provides **Download bundle** and **Propose via GitHub**.
A downloaded bundle remains usable without sign-in or a reachable curation service.
To inspect it locally, use a clean checkout of the source commit:

```console
pixi run apply-editor-bundle -- "/path/to/orinoco-review-bundle.json"
```

Review the reported diff, then add `--write` to apply that exact bundle.
Run validation and the build checks before committing the YAML change.
The command rejects stale or conflicting inputs, invalid metadata, and changes outside the supported review boundary.
Without `--write`, it does not modify record YAML.

**Propose via GitHub** authenticates through the curation service and creates a pull request for trusted validation.
When the site has a configured source-adapter workflow, the static `/review/` interface presents its candidates and submits explicit review decisions through the same service.
Neither action changes accepted metadata until its pull request is reviewed and merged.
See [custom-domain setup](../docs/custom-domain.md) for hosted editing configuration.
Production choices remain subject to the [open decisions](https://github.com/ORINOCO-Lite/orinoco-lite-dev/blob/main/docs/agents/open-decisions.md).

## Adopt a reviewed release

Review the package, template, and reusable-workflow selections together with the frozen `pixi.lock`.
The `package` mapping in `orinoco.lock` records the wheel version, URL, and SHA-256 digest; `.copier-answers.yml` records `package_version`, `package_url`, and `package_sha256`.
Package code and bundled resources share that version and integrity boundary.

Adopt a reviewed template release through a focused pull request.
Keep site inputs and adapters unchanged unless the pull request explicitly includes a reviewed site-owned change.
Run validation and build checks, review the resulting site, and follow the repository's human review and merge policy.
Deferring an update leaves the default branch and deployment unchanged.
A rollback reverts the reviewed update commit and restores the matching scaffold and release selections together.

## Documentation above this layer

- [Orinoco Lite template](https://github.com/ORINOCO-Lite/orinoco-lite-template): scaffold creation and maintenance.
- [Project design charter](https://github.com/ORINOCO-Lite/orinoco-lite-dev/blob/main/docs/project-design.md): system responsibilities and data flows.
- [Orinoco Lite package](https://github.com/ORINOCO-Lite/orinoco-lite-dev/tree/main/packages/orinoco-lite): commands and package integrity.
- [Orinoco Lite releases](https://github.com/ORINOCO-Lite/orinoco-lite-dev/releases): immutable wheels containing code and bundled resources.

Those shared layers do not own CON records, site-specific policy, or this site's provenance.
