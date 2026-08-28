# Metadata review runner

`metadata-review` runs the version-1 source manifests found at
`site-specific/sources/*/source.yaml`. Each manifest declares at least:

```yaml
contract_version: 1
id: example
adapter: extensions/source-adapters/example/metadata_adapter.py
provenance_identity: xyzrins:source-adapters/example/v1
enabled_by_default: false
```

An adapter may add its own reviewed configuration. It may replace only
captured content or evidence below its own `site-specific/sources/<id>/`
directory; it never promotes canonical records. Use `--only ID` for an
explicit source and `refresh-evidence` only after reviewing its report.

```console
./.orinoco-lite/source-adapters/metadata/metadata-review review -- --only zotero
```
