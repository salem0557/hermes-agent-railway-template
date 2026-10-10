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
- For a file that will be edited repeatedly, create or update its live copy with `hermes-storage sync LOGICAL_KEY --from-file LOCAL_FILE --force`.
- Open the latest live copy with `hermes-storage open LOGICAL_KEY`. Always use the returned local path.
- After editing that path, run `hermes-storage sync LOGICAL_KEY`. This preserves the previous remote copy as a dated version before publishing the update.
- When the task is finished and the latest sync succeeded, run `hermes-storage release LOGICAL_KEY` to remove only the verified local copy.

Only paths under `HERMES_WRITE_SAFE_ROOT` are accepted. Never upload credentials, environment files, private keys, databases, session state, authentication data, or tokens. The helper rejects common sensitive paths and formats.

Uploads copy data and never remove the local source. Downloads are atomic. The cache is disposable and limited by `HERMES_STORAGE_CACHE_MAX_MB`; only generated cache files may be pruned. There is intentionally no command for deleting Bucket objects.

`open`, `sync`, and `release` manage live working files under the `workspace/current` Bucket prefix. `open` checks the remote ETag and refuses to overwrite unsynced local changes. `sync` verifies SHA-256 and size, and preserves the previous remote object under `workspace/versions`. `release` refuses to remove a local file unless it exactly matches the verified current Bucket copy. Package-manager cache archives are dated recovery snapshots; they are not the live source and do not need to be kept current.
