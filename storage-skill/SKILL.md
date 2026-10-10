---
name: railway-storage
description: Store large user artifacts in the connected Railway Bucket and fetch them into a bounded local cache only when needed.
---

# Railway Bucket storage

Use `hermes-storage` for large user-created artifacts that do not need to stay on the `/data` volume.

- Check access with `hermes-storage status`.
- Inspect objects with `hermes-storage list [PREFIX]`.
- Upload a file with `hermes-storage put LOCAL_FILE OBJECT_KEY`.
- Fetch an object for current work with `hermes-storage cache OBJECT_KEY`. Use the returned local path.
- Download to a specific location with `hermes-storage get OBJECT_KEY LOCAL_FILE`.

Only paths under `HERMES_WRITE_SAFE_ROOT` are accepted. Never upload credentials, environment files, private keys, databases, session state, authentication data, or tokens. The helper rejects common sensitive paths and formats.

Uploads copy data and never remove the local source. Downloads are atomic. The cache is disposable and limited by `HERMES_STORAGE_CACHE_MAX_MB`; only generated cache files may be pruned. There is intentionally no command for deleting Bucket objects.
