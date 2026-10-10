# Hermes Agent Railway Template

Deploy [Hermes Agent](https://github.com/NousResearch/hermes-agent) on Railway using the official Nous Research image, dashboard, and s6 process supervision. A minimal compatibility entrypoint maps variables from existing template deployments before handing control to the upstream `/init` process.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/hermes-agent)

## Railway configuration

The Dockerfile is pinned to this official image:

```text
nousresearch/hermes-agent:v0.21.6@sha256:55e192fba0cd4fde61142abbff5adeacff40efdb482ccd0ff877924bd274f909
```

The compatibility entrypoint delegates to the release's upstream entrypoint dispatcher so both normal PID-1 startup and Railway runtimes with an init wrapper are supported.

Use these service settings:

| Setting | Value |
|---|---|
| Start command | `/usr/local/bin/hermes-railway-entrypoint gateway run` |
| Volume mount | `/data` |
| Public port | `8080` |
| Health-check path | `/api/status` |
| Replicas | `1` |

Railway continues building this GitHub repository so existing template consumers receive update notifications. The compatibility entrypoint only translates legacy environment variables, then executes the official image's `/init`; s6-overlay still supervises the Hermes gateway and dashboard.

### Variables

```dotenv
PORT=8080
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<generated-password>
```

The compatibility entrypoint maps the existing `ADMIN_USERNAME` and sealed `ADMIN_PASSWORD` values to the official dashboard variables before starting `/init`. Existing deployments therefore keep the same login without adding, copying, revealing, or re-entering credentials. It does not modify `config.yaml`. If `ADMIN_PASSWORD` is absent, it generates and logs a password as the previous template did.

For zero-interaction migration, a stable 32-byte dashboard session-signing secret is derived from `ADMIN_PASSWORD`. Derivation supports legacy passwords shorter than Hermes's 16-byte minimum signing-key length while preserving the existing login. After migration, operators may add `HERMES_DASHBOARD_BASIC_AUTH_SECRET` as an independent sealed value containing at least 32 random bytes. Rotating only this secret logs dashboard users out; it does not change the dashboard password or Hermes data. Neither the password nor the derived secret is written to `config.yaml`.

The Dockerfile supplies the remaining official variables:

```dotenv
HERMES_HOME=/data/.hermes
HERMES_WRITE_SAFE_ROOT=/data/.hermes
HERMES_LAZY_INSTALL_TARGET=/data/.hermes/lazy-packages
HERMES_STORAGE_CACHE_DIR=/data/.hermes/storage-cache
HERMES_STORAGE_CACHE_MAX_MB=1024
HERMES_STORAGE_WORKSPACE_MAX_MB=512
HERMES_DASHBOARD=1
HERMES_DASHBOARD_HOST=0.0.0.0
HERMES_GATEWAY_BOOTSTRAP_STATE=running
```

The entrypoint maps `PORT` to `HERMES_DASHBOARD_PORT` at runtime.

Provider credentials, messaging channels, models, skills, profiles, and gateway state are managed through the official dashboard and persisted under `/data/.hermes`.

## Railway Bucket storage

The image includes a small `hermes-storage` client and a Hermes skill for moving large user artifacts to a Railway Bucket. The Bucket is object storage, so an object is downloaded into a bounded local cache before a tool can open or modify it.

Add these variables to the Hermes service as Railway reference variables, replacing `hermes-storage` only if the Bucket service has a different name:

```dotenv
HERMES_STORAGE_BUCKET=${{hermes-storage.BUCKET}}
HERMES_STORAGE_ENDPOINT=${{hermes-storage.ENDPOINT}}
HERMES_STORAGE_ACCESS_KEY_ID=${{hermes-storage.ACCESS_KEY_ID}}
HERMES_STORAGE_SECRET_ACCESS_KEY=${{hermes-storage.SECRET_ACCESS_KEY}}
HERMES_STORAGE_REGION=${{hermes-storage.REGION}}
```

The values remain Railway references; do not copy the generated credentials into source control. On startup, the entrypoint installs the `railway-storage` skill only when the Bucket and endpoint variables are present.

Useful commands inside the Hermes service are:

```sh
hermes-storage status
hermes-storage list
hermes-storage put /data/.hermes/workspace/report.pdf reports/report.pdf
hermes-storage cache reports/report.pdf
hermes-storage sync reports/report.pdf --from-file /data/.hermes/workspace/report.pdf --force
hermes-storage open reports/report.pdf
hermes-storage sync reports/report.pdf
hermes-storage release reports/report.pdf
```

The helper accepts local paths only under `HERMES_WRITE_SAFE_ROOT`. It refuses common credential, private-key, database, authentication, and session-state files. Uploads do not remove local sources, downloads are atomic, and no remote-delete command is provided. `cache` keeps recently used objects under `HERMES_STORAGE_CACHE_DIR` and prunes only generated cache files when they exceed `HERMES_STORAGE_CACHE_MAX_MB`.

This facility is intended for user artifacts and archives. Hermes runtime state and package-manager installations still require the mounted volume and must not be moved while the service is running.

For files that change over time, `sync` publishes the latest verified content under `workspace/current`, while first copying the previous remote object to `workspace/versions`. `open` checks the current remote ETag before returning a bounded local working copy, and it refuses to overwrite unsynced local changes. After a successful sync, `release` removes only a byte-for-byte verified local working copy. This gives Hermes an on-demand working set without treating a dated archive as current data.

Package-manager caches are different: they are reproducible downloads, so dated cache archives remain recovery snapshots rather than a continuously synchronized source. A later snapshot may be created before another cleanup, but ordinary Python and npm cache changes do not need to be uploaded after every package operation.

## Upgrading Hermes

Update the pinned release and digest in `Dockerfile` deliberately after reviewing the upstream [release notes](https://github.com/NousResearch/hermes-agent/releases) and validating the new image. Do not use `latest`. Because the template remains GitHub-backed, merging an upgrade to the default branch notifies existing template consumers.

See the official [Hermes Docker documentation](https://hermes-agent.nousresearch.com/docs/user-guide/docker) for image behavior and configuration details.
