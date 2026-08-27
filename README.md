# Center for Open Neuroscience — Orinoco Test Site

Test-only full-content Orinoco Lite downstream for the Center for Open Neuroscience.

This is an ordinary single-repository Orinoco Lite consumer.
Metadata records, editorial content, declared assets, presentation, source adapters, extensions, and site tests are versioned directly here.
Building, previewing, and updating it requires neither Git submodules nor an engineering workspace checkout.

## Framework and site boundaries

- This repository owns its content, presentation, policy, tests, review, and deployment; if present, `site/README.md` is its site-owned guide for concrete editorial and operating procedures.
- The [Orinoco Lite template](https://github.com/ORINOCO-Lite/orinoco-lite-template) owns the generic repository facade, file-ownership contract, and content-preserving updater.
- The [Orinoco Lite engine](https://github.com/ORINOCO-Lite/orinoco-lite-dev) implements the commands, runtime verification, projection, and static build.

`orinoco.lock` is the release authority.
It records exact engine, runtime, template, and reusable-workflow coordinates; the frozen `pixi.lock` realizes that reviewed environment.
PyPI publication is optional and is not required by this repository.

Start with [creation and configuration](docs/getting-started.md) for the required site-profile step, [custom-domain and curation setup](docs/custom-domain.md) before enabling hosted editing, [file ownership](docs/ownership.md) before customizing the facade, and [framework updates](docs/updating.md) before changing release pins.

## Rights and intended use

The generic Orinoco Lite facade is MIT licensed and its original documentation is CC BY 4.0.
Those terms do not license site-owned records, editorial prose, media, branding, presentation, or imported third-party material.
Document those rights separately and preserve every upstream notice.
See engine human-review decision [HR-003](https://github.com/ORINOCO-Lite/orinoco-lite-dev/blob/main/docs/human-review-decisions.md#hr-003--establish-authority-and-a-project-license-matrix).

## Routine commands after adding a site profile

```console
pixi run validate
pixi run hugo-projection-update
pixi run projection-verify
pixi run assets-hydrate
pixi run assets-verify
pixi run build
pixi run serve
pixi run test
pixi run test-all
pixi run update-check
```

After editing metadata records, `validate` regenerates the ignored projection and checks it.
`build` does the same before rendering, so the source commit shows the metadata change rather than a duplicate generated tree.
`assets-hydrate` is the explicit networked retrieval step for declared remote assets; `assets-verify` checks already-local payloads without fetching.

## Build targets

`pixi run build` writes `build/site` with root-relative links.
`pixi run serve` serves that existing artifact on port 8765; it does not rebuild it.
The same files therefore work at both `http://127.0.0.1:8765/` and `http://localhost:8765/`.

Pages is intentionally separate.
The Pages workflow obtains the destination's absolute public base URL from GitHub, validates it, and passes it to `pixi run build-pages`, which writes `build/pages`.
After deployment succeeds, it force-updates one generated commit containing the complete projection at `latest-hugo-projection` and a child commit containing only the deployed site at `gh-pages`.
Both commits descend from the exact deployed default-branch commit and are retained for debugging, while `main` remains source-only.
Browser acceptance uses a controlled local project-path URL matching the repository slug.
Neither target changes the canonical public identity recorded in `orinoco.yaml`.

`pixi run test-all` is the complete acceptance gate: asset preparation, configuration and runtime validation, projection verification, the pinned Hugo Extended version, consumer tests, byte-compared repeat builds, dual-loopback local-link checks, and the checked Chromium/WebKit scenarios.
Its browser preparation makes both engines available before testing; on Linux, the post-Chromium WebKit host-library step and browser downloads are separate, logged, bounded phases.

## Network boundary

Hydration is the only asset command authorized to retrieve declared read-only payload URLs.
For a warmed-cache offline proof, run `assets-hydrate` while online, deny network access at the operating-system boundary, then run `assets-verify` before offline validation, projection, build, and editor checks.
Do not use `assets-prepare-online` in that denied-network phase; it represents the normal cold-clone preparation path.

## Repository content

- `metadata/records/` contains every human-facing YAML Thing used as projection
  input; `metadata/overlays/annotations/` contains its mirrored machine PAV
  companions when present.
- `.orinoco-lite/` contains implementation support behind the checked commands.
- `custom/editorial/`, `custom/assets/`, and `site/` contain site-owned presentation inputs.
- `.agents/skills/manage-orinoco-content/` guides agents through focused editorial and asset changes.
- `.agents/skills/operate-orinoco-metadata-adapters/` guides adapter runs,
  explicit human decisions, provenance, and review pull requests.
- `source-adapters/` contains optional site-owned importers, enrichers, and scrapers; it is not a deployed runtime dependency.
- `.github/workflows/shacl-vue-proposal.yml` is the generic trusted human-edit
  boundary; a concrete source-adapter curation workflow remains site-owned.
- The deployed static `/edit/` route is the sole SHACL Vue editor. It offers
  **Download bundle** and **Propose via GitHub**; the configured curation
  service supplies only the authenticated GitHub submission boundary. The
  central service is the default; `site.curation_service` is only a
  self-hosting override.
- The deployed static `/review/` route is the sole source-adapter decision
  interface. Source-adapter workflows link there from the trusted
  `site.base_url`; the selected curation service supplies only OAuth,
  verified GitHub reads, confirmation, and authenticated transport.
- `extensions/` is the stable downstream customization surface.
- `generated/` contains ignored projection output recreated by validation and builds.

A newly created repository is a content-neutral facade, not an empty but buildable website.
Add a reviewed site profile as described in [creation and configuration](docs/getting-started.md) before running validation or build commands.
A populated profile and source-adapter examples are available in the [downstream test website](https://github.com/ORINOCO-Lite/test-orinoco-downstream-website).
