# Pull-request previews on Netlify

This site publishes its canonical deployment from the default branch through
GitHub Pages. Netlify adds one extra, disposable rendering per pull request so
reviewers can read proposed metadata and content as a website before merging.
Curation proposal branches are reviewed the same way, even though the validate
workflow deliberately skips their builds.

`netlify.toml` builds only Netlify's `deploy-preview` context. Its production
and branch-deploy `ignore` commands cancel every other build, so Netlify never
publishes a second copy of the canonical site.

## Connect the repository once

1. In Netlify, choose **Add new site → Import an existing project** and select
this repository. Netlify reads `netlify.toml`, so leave the build command and
publish directory empty in the import form.
2. Keep the repository's default branch as the production branch. Netlify
cancels those builds and only builds pull requests.
3. Deploy previews are enabled by default for connected repositories. To change
that setting, use **Project configuration → Build & deploy → Continuous
Deployment → Branches and deploy contexts**. Preview Server settings do not
control deploy previews.
4. Open a pull request and check the Netlify status on it. The preview URL is
`https://deploy-preview-<number>--<netlify-site>.netlify.app/`.

## What a preview build does

The build command installs the same reviewed Pixi release the workflows use,
with Pixi's upstream installer, and runs `pixi run --frozen build`, this
repository's ordinary root-relative build. A preview is therefore exactly the
site that `pixi run build` and `pixi run serve` produce locally, from the same
frozen lock, and needs no preview-specific tooling.

A preview is not the deployed site:

- it is served from the deploy-preview URL rather than `identity.base_url`, so
  its pages are root-relative rather than canonical;
- it carries no trusted GitHub repository identity, so `/edit/` and `/review/`
  keep **Download bundle** and cannot start a **Propose via GitHub** submission;
  and
- it is rebuilt from scratch for every pull-request revision.

## Before enabling previews on a public repository

A preview build runs the `netlify.toml` of the pull request it renders,
including one opened from a fork. That build receives no repository secret and
cannot publish the canonical site, but it does spend the Netlify account's build
minutes, and a fork can change the Pixi release its own configuration installs.
Review Netlify's build-minute and concurrency limits, and leave deploy previews
disabled where untrusted contributors can open pull requests.

## Stop using previews

Re-run `copier update` and answer `none` for pull-request previews, then delete
the Netlify site. Removing `netlify.toml` alone leaves the repository connected
and lets Netlify infer a build.
