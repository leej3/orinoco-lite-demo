# Custom domain and secure GitHub submission

Orinoco Lite keeps editing on this deployed site.
The central curation service supplies only the GitHub App authentication and submission boundary; it does not host a second editor, review page, landing page, or copy of the site's presentation inputs.

## Choose the browser origin

A dedicated custom domain is the normal, low-friction configuration.
On that origin, `/edit/` and `/review/` can offer **Propose via GitHub** without an additional shared-origin acknowledgment.
**Download bundle** always remains available without GitHub authentication or a curation-service request.

GitHub project sites at `OWNER.github.io/REPOSITORY/` share the browser origin `https://OWNER.github.io` with every other path published on that host.
Browsers isolate origins, not URL paths, so `/REPOSITORY/edit/` cannot prove that another same-origin page is a different application.
Orinoco Lite therefore shows a clear warning on a shared `*.github.io` host and keeps direct GitHub submission disabled until the curator explicitly acknowledges that boundary.
The acknowledgment is in memory for the current page only; it is neither a credential nor a permanent site setting.

The curation service still revalidates the repository, installation, user permission, pull request, commits, and allowed paths.
The acknowledgment addresses the remaining browser-origin limitation rather than replacing those server-side checks.

## Add and verify a GitHub Pages custom domain

Use a hostname dedicated to this trusted site, then complete these steps:

1. Verify the domain for the owning GitHub account or organization before attaching it to the repository.
Follow GitHub's [domain-verification procedure](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/verifying-your-custom-domain-for-github-pages) and keep the verification TXT record in DNS.
2. In the site's repository, open **Settings → Pages**, add the custom domain, and save it before pointing DNS at GitHub Pages.
3. Configure the required DNS record with the domain provider by following GitHub's [custom-domain guide](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).
Do not use a wildcard DNS record.
This template publishes with GitHub Actions, so it neither needs nor tracks a `CNAME` file.
4. Wait for GitHub's DNS and certificate checks to succeed, then enable **Enforce HTTPS** using GitHub's [HTTPS procedure](https://docs.github.com/en/pages/getting-started-with-github-pages/securing-your-github-pages-site-with-https).
5. Set `site.base_url` in `orinoco.yaml` and `site_base_url` in `.copier-answers.yml` to the exact canonical HTTPS URL, including any path and a trailing slash.
Commit that identity change and let the Pages workflow deploy it.

For a GitHub Actions Pages deployment, repository settings are authoritative for the custom domain.
A tracked `CNAME` file is ignored and is not a substitute for those settings.

## Verify the deployed behavior

After the Pages workflow succeeds:

1. Open the deployment URL reported by GitHub and confirm that it redirects, if necessary, to the exact HTTPS custom domain recorded in `site.base_url`.
2. Open the deployed `/edit/` route and confirm **Download bundle** works before signing in.
3. Confirm the shared-`github.io` warning is absent and **Propose via GitHub** can start the GitHub App popup normally.
4. Open `/review/` from a real source-review link and confirm it remains on the same custom origin throughout review and submission.

If the page is still served from `*.github.io`, do not suppress the warning in site configuration.
Either use **Download bundle**, or read and acknowledge the explanation before starting direct GitHub submission.

## Use a self-hosted curation service

The central Orinoco Lite service at `https://orinoco-curation-review.pages.dev` is used when `site.curation_service` is absent; do not repeat that default in site configuration.
To replace only that backend, add its credential-free HTTPS origin to `orinoco.yaml`:

```yaml
site:
  curation_service: https://curation.example.org
```

Do not add repository identity beside it.
The Pages build continues to derive that identity from the trusted GitHub Actions context.
Use the engineering repository's [GitHub App deployment skill](https://github.com/ORINOCO-Lite/orinoco-lite-dev/tree/main/.agents/skills/orinoco-github-app-deployment) to configure the replacement service, its GitHub App callback, exact downstream-origin checks and binding, and deployment verification.
