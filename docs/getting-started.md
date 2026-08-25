# Creating and configuring a site

This repository is a consumer of two versioned framework layers:

- the [Orinoco Lite engine](https://github.com/con/orinoco-lite-dev) implements validation, projection, build, preview, and runtime verification; and
- the [Orinoco Lite template](https://github.com/con/orinoco-lite-template) supplies this repository facade, ownership contract, pinned coordinates, and update workflow.

The consumer owns its records, presentation, policy, tests, review, and deployment.
Framework updates do not choose or rewrite those things.

**Rights notice:** the generic Orinoco Lite facade is MIT licensed and its original documentation is CC BY 4.0.
Those terms do not license site-owned or third-party content, media, branding, or presentation.
Record those rights separately and preserve every upstream notice, as required by accepted engine human-review decision [HR-003](https://github.com/con/orinoco-lite-dev/blob/main/docs/human-review-decisions.md#hr-003--establish-authority-and-a-project-license-matrix).

**Historical release notice:** immutable tags `v0.1.10` and `v0.1.11` retain `v0.1.9` in their embedded Copier answers and lock metadata.
Do not use those two tags for a new Copier-first site or `copier recopy`.

Use `v0.1.12` or a later internally aligned release for Copier-first creation.

## Copier-first creation

When the site name and Pages project path are known, create it from an exact immutable template release:

```console
copier copy --vcs-ref vX.Y.Z gh:con/orinoco-lite-template new-site
```

Replace `vX.Y.Z` with a tag from the [template releases](https://github.com/con/orinoco-lite-template/releases).
Answer every prompt before adding site content.
The result records the exact tag in `.copier-answers.yml` and receives the release's reviewed frozen lock.

## GitHub-template creation

For a GitHub-first review:

### 1. Create the repository

Select **Use this template** in the template repository and leave **Include all branches** unchecked.
Only the rendered consumer branch belongs in the new repository.

### 2. Configure the recorded identity

Clone the new repository and leave all release coordinates unchanged.
In `.copier-answers.yml`, edit only `project_name`, `project_slug`, `site_description`, `repository_slug`, and `site_base_url`.
In particular, do not change `_commit`, `template_version`, or any engine, runtime, or workflow pin.

### 3. Render that identity consistently

Make the same identity changes in these locations:

- the heading and description in `README.md`;
- the workspace name and three browser-project-path values in `pixi.toml`; and
- the `site` name, description, and canonical base URL in `orinoco.yaml`.

### 4. Refresh and review

Refresh the lock, review the complete bootstrap diff, and check for neutral placeholders:

```console
pixi lock
rg -n 'Orinoco Lite Site|orinoco-site|example/orinoco-site|example.invalid' \
  .copier-answers.yml README.md orinoco.yaml pixi.toml
```

An empty search is expected unless one of those strings is deliberately part of the site's reviewed identity.
Do not run `copier recopy` on a repository made with the GitHub button; the published consumer tree may contain reviewed patch states newer than its Copier base, and recopy would discard them.

### 5. Commit the identity bootstrap

Commit this identity bootstrap before authoring content.
Do not run `validate` or `build` yet: the content-neutral repository facade is not itself a site profile.

## Add a site profile

Before the normal commands can run, add one reviewed site profile containing:

- schema-compatible Things YAML under `metadata/records/`;
- `site/projection.yaml`, its declared templates and graph producer;
- the site framework/configuration expected by the selected presentation; and
- a valid asset manifest plus any required editorial and presentation inputs.

Every file under `metadata/records/`, apart from the optional root `.dumpthings.yaml` control marker, must be a real Thing.
The template therefore does not create that otherwise empty directory with a `.gitkeep` placeholder.

The current complete example is the [downstream test website](https://github.com/con/test-orinoco-downstream-website).
The lightweight architecture roadmap tracks a future neutral starter profile; until one is released, selecting or authoring a profile is an explicit creation step rather than hidden template content.

After the profile is present, install and exercise the locked facade:

```console
pixi install --frozen
pixi run validate
pixi run build
pixi run serve
```

## Configure hosted human editing

The template ships the generic trusted workflow that validates SHACL Vue
handoffs and publishes an expiring editor-input artifact for an exact metadata
commit. It uses the central review service by default. To use a compatible
self-hosted deployment, set the repository Actions variable
`CURATION_REVIEW_APP_ORIGIN` to its credential-free HTTPS origin, with no path,
query, or fragment.

The central service keeps its workflows distinct: source-adapter decision
reviews open under `/review/`, while the trusted SHACL Vue wrapper opens under
`/edit/`. The downstream site's own `/edit/` route remains the credential-free
SHACL Vue **Download bundle** workflow and does not gain GitHub semantics.

Concrete source-adapter acquisition, candidate policy, and
`.github/workflows/curation-review.yml` remain site-owned. Adding those pieces
does not transfer `source-adapters/`, decision caches, records, or annotation
companions to template ownership.

The GitHub-template route starts with neutral placeholder identity.
Do not deploy it unchanged.
Later framework changes use the updater described in [Framework updates](updating.md).

## What to read next

- [File ownership](ownership.md) explains which paths the template may update.
- The repository README explains local, Pages, and acceptance build targets.
- A populated profile and source-adapter example is available in the [downstream test website](https://github.com/con/test-orinoco-downstream-website).
