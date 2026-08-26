# Metadata source adapters

The Milestone 5 curation host and GitHub workflow are described in [`GITHUB-CURATION.md`](GITHUB-CURATION.md).
They use the shared Orinoco candidate, annotation, decision-cache, and finalization APIs while keeping site mapping policy in each adapter.
Each source declares its reviewed adapter provenance identity in trusted
`sources.toml`.
The GitHub workflow derives that value after the operator selects a source; it
is not a dispatch input or override.

The older read-only `metadata-review` command remains available for inspecting the accepted Milestone 3 source evidence.
It is not the Milestone 5 proposal, decision, or finalization interface.

Run a read-only review of every configured live source against its committed source evidence, transformed candidates, and canonical YAML:

```console
./source-adapters/metadata/metadata-review review
```

The command writes ignored JSON, Markdown, fetched-source, candidate, and canonical-impact artifacts below `build/metadata-review/`.
It never changes tracked files.

After inspecting that report, explicitly refresh only the committed source snapshot and deterministic source candidates:

```console
./source-adapters/metadata/metadata-review refresh-evidence
```

The wrapper requires Pixi and always uses the committed lock plus a detached environment.
This keeps executables and symlinks out of the repository's `source-adapters/` tree that Orinoco validates and packages.
Pixi creates a temporary workspace link to that detached environment; the wrapper removes that link on every exit while retaining the cached environment itself.

That command still does not change `metadata/records/**`.
Commit the evidence refresh on a dedicated branch, run the complete consumer test suite, and open an ordinary metadata-review pull request.
A reviewer may then promote selected candidates in a separate content commit, refresh the projection, and review the rendered impact.

That read-only host loads the modules declared in `sources.toml`.
Adapters and their source policy remain site-specific and live in this repository.

Adapters that require a caller-pinned checkout are opt-in.
Select one and pass its input explicitly after Pixi's argument delimiter:

```console
./source-adapters/metadata/metadata-review review -- \
  --only dump-research-info \
  --source-input dump-research-info=/path/to/dump-research-info
```

For the supported metadata proposal, hosted review, and trusted finalization entry point, use [GitHub source curation](GITHUB-CURATION.md).
Direct `refresh-evidence` remains outside that Milestone 5 workflow.
