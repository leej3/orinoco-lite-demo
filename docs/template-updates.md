# Update the template

In GitHub, open **Actions → Update downstream template → Run workflow**.
Select the branch to update and enter a template commit, tag, or branch.
The command records the resolved commit and selects the package declared by that template.
Optional package revision and repository inputs apply an explicit override in a separate commit.

The workflow opens a draft pull request containing the DataLad-recorded changes and validation results.
Review it, resolve any conflicts, and merge when ready.
An unchanged selection produces no pull request.
Site inputs under `site-specific/`, submodule selections, `extensions/`, and `orinoco.yaml` remain site-owned.

Repository administrators must enable **Settings → Actions → General → Allow GitHub Actions to create and approve pull requests**.
Updates that change `.github/workflows/` require the `ORINOCO_UPDATE_TOKEN` Actions secret with repository contents, pull request, and workflow write access; the default Actions token cannot push those changes.
GitHub may ask a maintainer to approve validation runs on the generated pull request.

## Resolve conflicts in GitHub

Conflicted updates are committed deliberately so they can be reviewed in the browser.
The workflow reports the affected files and leaves the pull request as a draft.
Open each file on the pull request branch, choose **Edit**, retain the intended content, and remove the `<<<<<<<`, `=======`, and `>>>>>>>` marker lines.
Commit the resolution to that branch and check the validation results.
These edits record the human resolution separately from the generated update.

If `pixi.toml` conflicted, resolve it first, then run **Update downstream template** on that branch with the same selections to regenerate its lock.
Set **environment_revision** to a known-good branch such as `main` so the updater can run before the edited manifest has a matching lock.
Review and merge the resulting follow-up pull request into the update branch before merging the original update.
Local resolution is also supported.

## Update locally

Start from a clean checkout:

```console
pixi run orinoco-lite template update --revision TEMPLATE_REVISION
pixi run build
pixi run orinoco-lite verify-site build/site
```

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
