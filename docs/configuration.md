# Configure your site

Edit root `pyproject.toml` for runtime settings under `[tool.orinoco]`.
`site.identity`, `site.navigation`, and `site.appearance` control the public website.
Keep environment dependencies, package selections, and tasks in `pixi.toml`.
Copier answers record generation choices; the root manifest is authoritative afterward and template updates preserve it.

After installing the GitHub App, follow its setup page to choose permitted operations.
Add or edit this table on your repository's default branch, enabling only the operations you want:

```toml
[tool.orinoco.operations]
shacl_materialization = false
automated_curation = false
template_updates = false
preview_editing = false
```

These allow editor proposal materialization, automated curation completion, template-update proposals, and editing from verified development previews, respectively.
Missing choices remain disabled.
See the [operation and permission specification](https://github.com/ORINOCO-Lite/orinoco-lite-dev/blob/main/docs/agents/contract/curation-service-authentication-options.md#functionality-and-permissions) for required permissions and their purpose.
The service reads these choices from the current default branch; proposed changes cannot authorize themselves.

The central service is used by default.
For an independent deployment, set its HTTPS origin in `[tool.orinoco.service]` as `url = "https://curation.example.org"`.
GitHub Actions supplies repository identity; `[tool.orinoco.github]` may set `repository = "owner/repository"` for local builds.
Optional `[tool.orinoco.paths]` overrides can select `records`, `editorial`, and `extensions` directories.
