# Framework updates

A framework update changes the pinned template, engine, runtime, and reusable workflow as one reviewable unit.
It does not discover mutable "latest" aliases, merge itself, or silently rewrite site-owned content.

Review the target [template release](https://github.com/ORINOCO-Lite/orinoco-lite-template/releases) and [engine/runtime release](https://github.com/ORINOCO-Lite/orinoco-lite-dev/releases) before starting.
Record every exact version, URL, digest, and workflow commit.

## Check without changing the checkout

```console
pixi run update-check
```

The check uses the exact release recorded in `.copier-answers.yml` unless target coordinates are supplied.
It leaves the worktree unchanged.

## Apply exact reviewed coordinates

Start from a clean worktree.
Commit or stash unrelated work first.

```console
pixi run update-orinoco -- \
  --to-template v0.2.0 \
  --to-engine 0.2.0 \
  --engine-url https://github.com/example/releases/download/v0.2.0/orinoco_lite-0.2.0-py3-none-any.whl \
  --engine-sha256 <64-hex-digest> \
  --to-runtime 0.2.0 \
  --runtime-url https://github.com/example/releases/download/v0.2.0/runtime.tar.gz \
  --runtime-sha256 <64-hex-digest> \
  --runtime-manifest-sha256 <64-hex-digest> \
  --workflow-sha <40-hex-commit> \
  --workflow-ref owner/repository/.github/workflows/orinoco-consumer-ci.yml@<40-hex-commit>
```

The updater:

1. resolves the current and target template tags to peeled commits;
2. snapshots all protected site-owned files;
3. renders both releases to prove any pre-applied template bootstrap is three-way equivalent;
4. runs Copier with `.rej` conflicts;
5. updates `orinoco.lock`, `.copier-answers.yml`, and the frozen `pixi.lock`;
6. proves protected bytes did not change; and
7. writes an ignored diagnostic ledger under `.orinoco-lite/state/`.

It stops on a moving or unavailable tag, an incomplete pin, a template-owned conflict, or an undeclared protected change.
A newly introduced `.gitkeep` may be removed only when the protected directory already contains real data.

Consumers older than template v0.1.3 require the narrow updater bootstrap documented by that target release.
Moving an existing site's records, provenance, editorial files, assets, source
configuration, or decisions into `site-specific/` is a one-time semantic layout
migration, not an ordinary framework update: review and merge that dedicated
migration with the matching engine/template candidate.
Afterward, the normal Pixi task invokes `.orinoco-lite/tools/update_orinoco.py` for subsequent updates.
Do not bootstrap site-owned paths.
Browser tests under `.orinoco-lite/tests/browser/` are protected in the same way.
Pre-apply and review any site-specific compatibility repair before the framework update; for a Sigma graph scenario, validate the representative node in the serialized graph JSON and navigate through that node's URL rather than assuming the canvas renderer exposes a DOM link.

The engine's open-reference default is not a site-policy migration.
An existing `references.missing_targets: reject` or `graph.missing_external_targets: reject` remains byte-identical and strict through an update.
To adopt preservation and dropped graph-view edges, review a separate site-owned change that removes those explicit overrides, then validate the resulting reference and edge diagnostics.

## Review and commit

After a successful update:

```console
pixi run test-all
pixi run python .orinoco-lite/tools/finalize_update_ledger.py \
  --status passed \
  --command "pixi run test-all"
git diff --check
git status --short
```

Review at least:

- the complete `orinoco.lock`, `.copier-answers.yml`, and `pixi.lock` diffs;
- confirmation that protected site-owned paths did not change;
- any `.rej` file or recorded reconciliation;
- validation status and commands; and
- the generated site and browser behavior appropriate to this consumer.

Commit all update outputs as one focused commit.
The explicit GitHub update workflow performs the same transition, runs `test-all`, finalizes the ledger, and opens an ordinary review pull request.
It runs only through `workflow_dispatch` and never merges the pull request.
The workflow-created commit uses the automation identity supplied by the pinned pull-request action and does not claim a human or Codex co-author.

Security updates use the `security` classification to communicate urgency; they retain the same review and merge boundary.

## Defer, abandon, or roll back

### Defer an unmerged update

Leave the pull request open or close it.
The deployed/default branch remains unchanged.

### Abandon an uncommitted local update

Because the updater required a clean starting tree, first inspect `git status --short`, then restore tracked files and preview untracked cleanup:

```console
git restore --source=HEAD --staged --worktree -- .
git clean -nd
```

If the preview contains only files created by the abandoned update, remove exactly those files or run `git clean -fd`.
Never use that cleanup when the starting tree contained independent untracked work.

### Roll back a merged update

Revert the complete update commit with `git revert <update-commit>`, review the inverse diff, run `pixi run test-all`, and merge the revert normally.
Do not run Copier backward or move release tags.

Reverting the single update commit restores its previous facade, answers, and locks together.
If the update included an explicit semantic migration, the revert also includes that declared content diff; review its domain meaning instead of assuming it is mechanically safe.

## Ownership exception for semantic migrations

A normal update may not change protected content.
If a reviewed schema change requires a semantic migration, pass an explicit `--migration ID=summary` and one `--allow-site-change PATH` for every permitted path.
The updater records the exact before/after hashes and leaves the ledger in `human-review` status.
This is an exception requiring domain review, not a general overwrite switch.
