# Update the template

In GitHub, open **Actions → Update downstream template → Run workflow**.
Select the branch to update.
**Latest release** is selected by default and uses Copier’s latest version tag, including release candidates.
Choose **main** for the current development branch, or **Specific commit** and enter a full commit hash.
The command records the resolved commit and selects the package declared by that template.
Optional package revision and repository inputs apply an explicit override in a separate commit.

The workflow opens a draft pull request containing the DataLad-recorded changes and validation results.
Review it, resolve any conflicts, and merge when ready.
An unchanged selection produces no pull request.
Site inputs under `site-specific/`, submodule selections, `extensions/`, and `pyproject.toml` remain site-owned.

Install the central Orinoco Lite GitHub App on the downstream repository.
After App installation, set `template_updates = true` under `[tool.orinoco.operations]` in root `pyproject.toml` on the default branch.
See [Configuration](configuration.md) for the operation choices.
Changing Copier answers or an update branch does not authorize the service.
The service reports whether a refused update needs a downstream opt-in or an App installation permission.
Existing site-owned operation choices remain unchanged by template updates.
The App’s bot opens the pull request; no downstream personal token or private key is needed.
The workflow authenticates to the service through GitHub Actions and obtains temporary access only in its separate publishing job.
The App installation must grant contents, pull request, and workflow write permissions.
The Actions run summary links to the resulting draft and reports validation.

## Resolve conflicts in GitHub

Conflicted updates are committed deliberately so they can be reviewed in the browser.
The workflow reports the affected files and leaves the pull request as a draft.
Open each file on the pull request branch, choose **Edit**, retain the intended content, and remove the `<<<<<<<`, `=======`, and `>>>>>>>` marker lines.
Commit the resolution to that branch and check the validation results.
These edits record the human resolution separately from the generated update.

If `pixi.toml` conflicted, resolve it first, then run **Update downstream template** on that branch with the same selections to regenerate its lock.
The workflow on the recovery branch must match the current default branch; restore that workflow file from the default branch if the update changed it.
Set **environment_revision** to a known-good branch such as `main` so the updater can run before the edited manifest has a matching lock.
Review and merge the resulting follow-up pull request into the update branch before merging the original update.
Local resolution is also supported.

## Update locally

Start from a clean checkout:

```console
pixi run orinoco-lite template update
pixi run build
pixi run orinoco-lite verify-site build/site
```

Use `--revision main` or `--revision COMMIT` for a different template selection.
Add `--package-revision` and, when needed, `--package-repository` for an override.
Exit status 1 means the update was recorded with conflicts; resolve and commit those files before validation.
Other failures leave diagnostic output and any partial changes available for inspection.
The command never pushes, creates a pull request, or publishes the website.

## Replay and rollback

DataLad records `orinoco-lite template apply` with immutable selections.
For historical replay, use another clone and restore the recorded execution environment and submodule selections before `datalad rerun`.
GitHub runs record the repository and commit supplying the updater's `pixi.toml` and `pixi.lock`; local runs normally use the recorded parent commit's environment.
The package performing an update can differ from the package it selects; validation starts with a fresh Pixi invocation.
Replaying the generated update does not reproduce later human conflict resolutions.

To undo an adopted update in GitHub, use **Revert** on its merged pull request and review the resulting pull request.
Locally, use `git revert` on the update commits, including any package override commit.
Resolve conflicts and validate before merging the reversal.
This restores the recorded template answers, dependency selection, and files together; Copier itself rejects downgrade updates.
