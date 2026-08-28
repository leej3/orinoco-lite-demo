# Creating and configuring a site

This repository is a consumer of two versioned framework layers:

- the [Orinoco Lite engine](https://github.com/ORINOCO-Lite/orinoco-lite-dev) implements validation, projection, build, preview, and runtime verification; and
- the [Orinoco Lite template](https://github.com/ORINOCO-Lite/orinoco-lite-template) supplies this repository facade, ownership contract, pinned coordinates, and update workflow.

The consumer owns its records, presentation, policy, tests, review, and deployment.
Framework updates do not choose or rewrite those things.

**Rights notice:** the generic Orinoco Lite facade is MIT licensed and its original documentation is CC BY 4.0.
Those terms do not license site-owned or third-party content, media, branding, or presentation.
Record those rights separately and preserve every upstream notice, as required by accepted engine human-review decision [HR-003](https://github.com/ORINOCO-Lite/orinoco-lite-dev/blob/main/docs/human-review-decisions.md#hr-003--establish-authority-and-a-project-license-matrix).

**Historical release notice:** immutable tags `v0.1.10` and `v0.1.11` retain `v0.1.9` in their embedded Copier answers and lock metadata.
Do not use those two tags for a new Copier-first site or `copier recopy`.

Use `v0.1.12` or a later internally aligned release for Copier-first creation.

## Copier-first creation

When the site name and Pages project path are known, create it from an exact immutable template release:

```console
copier copy --vcs-ref vX.Y.Z gh:ORINOCO-Lite/orinoco-lite-template new-site
```

Replace `vX.Y.Z` with a tag from the [template releases](https://github.com/ORINOCO-Lite/orinoco-lite-template/releases).
Answer every prompt before adding site content.
The result records the exact tag in `.copier-answers.yml` and receives the release's reviewed frozen lock.

## GitHub-template creation

For a GitHub-first review:

### 1. Create the repository

Select **Use this template** in the template repository and leave **Include all branches** unchecked.
Only the rendered consumer branch belongs in the new repository.

### 2. Configure the recorded identity

Clone the new repository and leave all release coordinates unchanged.
In `.copier-answers.yml`, edit only `project_name`, `project_slug`, `site_description`, and `site_base_url`.
In particular, do not change `_commit`, `template_version`, or any engine, runtime, or workflow pin.

### 3. Render that identity consistently

Make the same identity changes in these locations:

- the heading and description in `README.md`;
- the workspace name and three browser-project-path values in `pixi.toml`; and
- the `site` name, description, and canonical base URL in `orinoco.yaml`; and
- identity, author, and webmanifest text in `site-specific/site.yaml`.

Do not add the GitHub repository as another site setting.
The trusted Pages build obtains it from GitHub's `GITHUB_REPOSITORY` context and writes the exact coordinate into the deployed editor and review configuration.

### 4. Refresh and review

Refresh the lock, review the complete bootstrap diff, and check for neutral placeholders:

```console
pixi lock
rg -n 'Orinoco Lite Site|orinoco-site|example.invalid' \
  .copier-answers.yml README.md orinoco.yaml pixi.toml
```

An empty search is expected unless one of those strings is deliberately part of the site's reviewed identity.
Do not run `copier recopy` on a repository made with the GitHub button; the published consumer tree may contain reviewed patch states newer than its Copier base, and recopy would discard them.

### 5. Commit the identity bootstrap

Commit this identity bootstrap before authoring content.
Do not run `validate` or `build` yet: the content-neutral repository facade is not itself a site profile.

## Add a site profile

The template already supplies a neutral structured site profile, its generic
framework, projection templates, graph producer, and an empty asset manifest.
Before the normal commands can run, complete the reviewed site layer with:

- schema-compatible Things YAML under `site-specific/metadata/records/`;
- an appropriate homepage record matching `site-specific/projection.yaml`;
- reviewed identity, navigation, people groups, project categories, and theme
  values in `site-specific/site.yaml`; and
- any bespoke editorial pages and declared assets under `site-specific/`.

Site-only executable Hugo overrides required by retained editorial content
belong under `extensions/site/layouts/`; reusable behavior should instead be
proposed to the template-owned framework.

Generic index pages for grouped people, all people, projects, publications, and
instruments are rendered from `site.yaml`. Keep only genuinely bespoke prose
under `site-specific/content/pages/`.

Every file under `site-specific/metadata/records/`, apart from the optional root `.dumpthings.yaml` control marker, must be a real Thing.
The template therefore does not create that otherwise empty directory with a `.gitkeep` placeholder.

The current complete example is the [downstream test website](https://github.com/ORINOCO-Lite/test-orinoco-downstream-website).

New site profiles inherit the engine's open-reference defaults.
Omitting the `references` section preserves well-formed references whose targets are not local, and omitting `graph.missing_external_targets` drops only graph-view edges whose targets cannot materialize locally.
Validation reports both preserved references and omitted edges without performing network lookup or creating identity records.

An existing site that intentionally requires local closure keeps that policy explicitly in its site-owned `site-specific/projection.yaml`:

```yaml
references:
  missing_targets: reject
graph:
  # Retain the site's producer, node_classes, and relationship_fields.
  missing_external_targets: reject
```

Framework updates preserve that file byte-for-byte and do not replace explicit `reject` policies with the new defaults.

After the profile is present, install and exercise the locked facade:

```console
pixi install --frozen
pixi run validate
pixi run build
pixi run serve
```

## Configure hosted human editing

The deployed downstream site's own `/edit/` route is the only SHACL Vue editor.
It offers both **Download bundle** and **Propose via GitHub**.
The credential-free download remains available without signing in or contacting the curation service.

The trusted Pages build derives the repository from GitHub's build context.
The Orinoco Lite central service is the default GitHub authentication and submission boundary, so a normal site does not configure either coordinate.
It may exchange credentials and create or update a pull request, but it does not host another editor or retain the site's presentation input.
To use a compatible self-hosted service, add one optional override to the site mapping in `orinoco.yaml`:

```yaml
site:
  curation_service: https://curation.example.org
```

Use a credential-free HTTPS origin with no path, query, or fragment.
Removing the override restores the central default.

The template-owned workflow validates a fixed-path review bundle submitted by the service, materializes the edit with trusted default-branch code, and replaces only that exact handoff commit.
It does not publish an editor-input Actions artifact.
Source-adapter decision reviews remain a separate workflow and use the deployed downstream site's own `/review/` route.
That route comes from trusted `site.base_url`; the selected curation service remains only its OAuth, verified GitHub-read, and authenticated-transport boundary.

For the normal low-friction direct-submission flow, give the site its own verified HTTPS custom domain.
A site served from a shared `*.github.io` origin instead explains the shared origin boundary and requires an explicit, in-memory acknowledgment before each page enables **Propose via GitHub**.
That acknowledgment is not a credential and is not stored; reloading the page requires it again.
See [Custom domain and secure GitHub submission](custom-domain.md) for the setup and verification checklist.

Generic adapter executables live under `.orinoco-lite/source-adapters/`.
Concrete acquisition coordinates, captured content, evidence, mapping policy,
and `.github/workflows/curation-review.yml` remain site-owned under
`site-specific/sources/`. Compact decisions remain under
`site-specific/curation-records/`; records and annotation companions remain
under `site-specific/metadata/`.

Each source uses one version-1 `site-specific/sources/<id>/source.yaml` with an
ID, adapter executable path, reviewed provenance identity, and explicit default
enablement. Run `pixi run source-adapter-canary` before networked source review.

The GitHub-template route starts with neutral placeholder identity.
Do not deploy it unchanged.
Later framework changes use the updater described in [Framework updates](updating.md).

## What to read next

- [File ownership](ownership.md) explains which paths the template may update.
- [Custom domain and secure GitHub submission](custom-domain.md) explains the direct-submission origin boundary and custom-domain setup.
- The repository README explains local, Pages, and acceptance build targets.
- A populated profile and source-adapter example is available in the [downstream test website](https://github.com/ORINOCO-Lite/test-orinoco-downstream-website).
