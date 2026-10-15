# PlanePlus publishing

PlanePlus builds publish only from `etaxi341/PlanePlus` to Docker Hub under
`johannmnn/planeplus-{frontend,space,admin,live,backend,proxy,aio-community}`.
The upstream feature-preview workflow is intentionally disabled in this fork.
The AIO image must embed these PlanePlus images, never `makeplane/plane-*`.

## Tags

- A push to `master` builds the six service images with `:latest`.
- A push to `preview` builds them with `:preview`.
- A manual **Release** builds the supplied version tag. Only a non-prerelease
  also updates `:stable`; regular branch builds do not change `:stable`.
- AIO is optional for branch builds and enabled for releases. Its component
  source tag matches the service tag (`latest`, `preview`, or the release version).

Before any images are published, the build workflow runs the configuration
regression tests. Run them locally with:

```sh
python -m pip install PyYAML==6.0.2
python -m unittest discover -s .github/tests -v
```

The shared build action is pinned to a commit so upstream tag changes cannot
silently change image tagging or publishing. Review that action and update the
tests when deliberately upgrading it. Keep the image names, repository guards,
and installer download repository when merging future upstream releases.

## v1.4.3 upgrade

This merge includes upstream's Django 5.2 upgrade and database migrations.
Back up the database and uploads before deployment, and run the migrator before
starting the updated services. PostgreSQL and upload volume names are unchanged.
