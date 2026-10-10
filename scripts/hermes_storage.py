#!/usr/bin/env python3
"""Safe, small Railway Bucket client for Hermes Agent."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile


REQUIRED_ENV = (
    "HERMES_STORAGE_BUCKET",
    "HERMES_STORAGE_ENDPOINT",
    "HERMES_STORAGE_ACCESS_KEY_ID",
    "HERMES_STORAGE_SECRET_ACCESS_KEY",
)
DENIED_NAMES = {
    ".env",
    "config.yaml",
    "credentials",
    "credentials.json",
    "secrets.json",
}
DENIED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key"}
DENIED_PARTS = {"sessions", "session", "auth", "tokens", "credentials", "secrets"}
WORKSPACE_CURRENT_PREFIX = "workspace/current"
WORKSPACE_VERSIONS_PREFIX = "workspace/versions"


class StorageError(RuntimeError):
    pass


def require_config() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise StorageError("Missing storage variables: " + ", ".join(missing))
    return {name: os.environ[name] for name in REQUIRED_ENV}


def normalize_key(value: str) -> str:
    value = value.replace("\\", "/").strip()
    path = PurePosixPath(value)
    normalized = path.as_posix()
    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or normalized == "."
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise StorageError("Object key must be a relative path without '.' or '..'")
    return normalized


def safe_root() -> Path:
    return Path(os.environ.get("HERMES_WRITE_SAFE_ROOT", "/data/.hermes")).resolve()


def require_safe_local_path(value: str, *, must_exist: bool = False) -> Path:
    path = Path(value).expanduser().resolve()
    root = safe_root()
    if path != root and root not in path.parents:
        raise StorageError(f"Local path must stay under {root}")
    if must_exist and not path.is_file():
        raise StorageError(f"Local file does not exist: {path}")
    return path


def require_uploadable(path: Path) -> None:
    lowered_parts = {part.lower() for part in path.parts}
    if path.name.lower() in DENIED_NAMES or path.suffix.lower() in DENIED_SUFFIXES:
        raise StorageError("Refusing to upload a credential, key, database, or session-state file")
    if lowered_parts & DENIED_PARTS:
        raise StorageError("Refusing to upload a file from a credential or session-state directory")


def require_safe_logical_key(value: str) -> str:
    key = normalize_key(value)
    path = PurePosixPath(key)
    lowered_parts = {part.lower() for part in path.parts}
    if path.name.lower() in DENIED_NAMES or path.suffix.lower() in DENIED_SUFFIXES:
        raise StorageError("Refusing a credential, key, database, or session-state object")
    if lowered_parts & DENIED_PARTS:
        raise StorageError("Refusing an object in a credential or session-state path")
    return key


def cache_root() -> Path:
    configured = os.environ.get("HERMES_STORAGE_CACHE_DIR", "/data/.hermes/storage-cache")
    return require_safe_local_path(configured)


def cache_path(key: str) -> Path:
    return require_safe_local_path(str(cache_root() / "objects" / normalize_key(key)))


def workspace_root() -> Path:
    return require_safe_local_path(str(cache_root() / "workspace"))


def workspace_path(key: str) -> Path:
    return require_safe_local_path(str(workspace_root() / require_safe_logical_key(key)))


def workspace_metadata_path(key: str) -> Path:
    digest = hashlib.sha256(require_safe_logical_key(key).encode("utf-8")).hexdigest()
    return require_safe_local_path(str(cache_root() / "workspace-meta" / f"{digest}.json"))


def current_object_key(key: str) -> str:
    return f"{WORKSPACE_CURRENT_PREFIX}/{require_safe_logical_key(key)}"


def version_object_key(key: str, etag: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    clean_etag = etag.strip('"') or "unknown"
    return f"{WORKSPACE_VERSIONS_PREFIX}/{require_safe_logical_key(key)}/{stamp}-{clean_etag}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_workspace_metadata(key: str) -> dict[str, object] | None:
    path = workspace_metadata_path(key)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def save_workspace_metadata(key: str, value: dict[str, object]) -> None:
    path = workspace_metadata_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def object_etag(head: dict[str, object]) -> str:
    return str(head.get("ETag", "")).strip('"')


def object_sha256(head: dict[str, object]) -> str:
    metadata = head.get("Metadata") or {}
    return str(metadata.get("sha256", "")) if isinstance(metadata, dict) else ""


def is_missing_object(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    return str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}


def head_or_none(client, key: str):
    try:
        return client.head_object(Bucket=bucket_name(), Key=key)
    except Exception as exc:
        if is_missing_object(exc):
            return None
        raise


def make_client():
    config = require_config()
    try:
        import boto3
    except ImportError as exc:
        raise StorageError("boto3 is unavailable in this image") from exc
    return boto3.client(
        "s3",
        endpoint_url=config["HERMES_STORAGE_ENDPOINT"],
        aws_access_key_id=config["HERMES_STORAGE_ACCESS_KEY_ID"],
        aws_secret_access_key=config["HERMES_STORAGE_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("HERMES_STORAGE_REGION") or "auto",
    )


def bucket_name() -> str:
    return require_config()["HERMES_STORAGE_BUCKET"]


def command_status(_args: argparse.Namespace) -> int:
    client = make_client()
    result = client.list_objects_v2(Bucket=bucket_name(), MaxKeys=1)
    print(json.dumps({"connected": True, "bucket": bucket_name(), "has_objects": bool(result.get("KeyCount"))}))
    return 0


def command_list(args: argparse.Namespace) -> int:
    client = make_client()
    result = client.list_objects_v2(
        Bucket=bucket_name(), Prefix=args.prefix, MaxKeys=args.limit
    )
    for item in result.get("Contents", []):
        modified = item["LastModified"].isoformat() if hasattr(item["LastModified"], "isoformat") else str(item["LastModified"])
        print(json.dumps({"key": item["Key"], "bytes": item["Size"], "modified": modified}))
    return 0


def command_put(args: argparse.Namespace) -> int:
    source = require_safe_local_path(args.local_file, must_exist=True)
    require_uploadable(source)
    key = normalize_key(args.key)
    make_client().upload_file(str(source), bucket_name(), key)
    print(json.dumps({"uploaded": key, "bytes": source.stat().st_size}))
    return 0


def download(key: str, destination: Path, *, force: bool) -> Path:
    if destination.exists() and not force:
        raise StorageError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".hermes-storage-", dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        make_client().download_file(bucket_name(), key, str(temporary))
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    os.utime(destination, None)
    return destination


def command_get(args: argparse.Namespace) -> int:
    key = normalize_key(args.key)
    destination = require_safe_local_path(args.local_file)
    result = download(key, destination, force=args.force)
    print(json.dumps({"downloaded": key, "path": str(result), "bytes": result.stat().st_size}))
    return 0


def command_cache(args: argparse.Namespace) -> int:
    key = normalize_key(args.key)
    destination = cache_path(key)
    if destination.is_file():
        os.utime(destination, None)
        prune_cache(args.max_mb, protected={destination})
        print(json.dumps({"cached": key, "path": str(destination), "hit": True}))
        return 0
    result = download(key, destination, force=False)
    size = result.stat().st_size
    prune_cache(args.max_mb, protected={result})
    print(json.dumps({"cached": key, "path": str(result), "hit": False, "bytes": size}))
    return 0


def prune_cache(max_mb: int, *, protected: set[Path] | None = None) -> tuple[int, int]:
    root = cache_root() / "objects"
    if not root.exists():
        return 0, 0
    protected = {path.resolve() for path in (protected or set())}
    files = [path for path in root.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    target = max_mb * 1024 * 1024
    removed = 0
    for path in sorted(files, key=lambda item: item.stat().st_mtime):
        if total <= target:
            break
        if path.resolve() in protected:
            continue
        size = path.stat().st_size
        path.unlink()
        total -= size
        removed += 1
    return removed, total


def command_prune(args: argparse.Namespace) -> int:
    removed, remaining = prune_cache(args.max_mb)
    print(json.dumps({"removed_cache_files": removed, "remaining_bytes": remaining, "max_mb": args.max_mb}))
    return 0


def default_workspace_max_mb() -> int:
    try:
        return max(0, int(os.environ.get("HERMES_STORAGE_WORKSPACE_MAX_MB", "512")))
    except ValueError as exc:
        raise StorageError("HERMES_STORAGE_WORKSPACE_MAX_MB must be an integer") from exc


def download_with_client(client, key: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".hermes-workspace-", dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        client.download_file(bucket_name(), key, str(temporary))
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    os.utime(destination, None)
    return destination


def workspace_file_is_clean(key: str, path: Path, metadata: dict[str, object] | None = None) -> bool:
    metadata = metadata or load_workspace_metadata(key)
    return bool(metadata and metadata.get("sha256") == sha256_file(path))


def prune_workspace(max_mb: int, *, protected: set[Path] | None = None) -> tuple[int, int]:
    root = workspace_root()
    if not root.exists():
        return 0, 0
    protected = {path.resolve() for path in (protected or set())}
    files = [path for path in root.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    target = max_mb * 1024 * 1024
    removed = 0
    for path in sorted(files, key=lambda item: item.stat().st_mtime):
        if total <= target:
            break
        if path.resolve() in protected:
            continue
        key = path.relative_to(root).as_posix()
        if not workspace_file_is_clean(key, path):
            continue
        size = path.stat().st_size
        path.unlink()
        workspace_metadata_path(key).unlink(missing_ok=True)
        total -= size
        removed += 1
    return removed, total


def command_open(args: argparse.Namespace) -> int:
    key = require_safe_logical_key(args.key)
    remote_key = current_object_key(key)
    client = make_client()
    head = head_or_none(client, remote_key)
    if head is None:
        raise StorageError(f"Live object does not exist: {key}")
    remote_etag = object_etag(head)
    remote_sha = object_sha256(head)
    destination = workspace_path(key)
    metadata = load_workspace_metadata(key)
    if destination.is_file() and metadata and metadata.get("etag") == remote_etag:
        if not workspace_file_is_clean(key, destination, metadata):
            raise StorageError(f"Local workspace file has unsynced changes: {destination}")
        os.utime(destination, None)
        prune_workspace(args.max_mb, protected={destination})
        print(json.dumps({"opened": key, "path": str(destination), "hit": True, "etag": remote_etag}))
        return 0
    if destination.is_file() and metadata is None:
        raise StorageError(f"Refusing to replace an unmanaged local file: {destination}")
    if destination.is_file() and metadata and not workspace_file_is_clean(key, destination, metadata):
        raise StorageError(f"Refusing to replace unsynced local changes: {destination}")
    download_with_client(client, remote_key, destination)
    local_sha = sha256_file(destination)
    if remote_sha and local_sha != remote_sha:
        destination.unlink(missing_ok=True)
        raise StorageError(f"Downloaded object failed SHA-256 verification: {key}")
    save_workspace_metadata(key, {"etag": remote_etag, "sha256": local_sha, "remote_key": remote_key})
    prune_workspace(args.max_mb, protected={destination})
    print(json.dumps({"opened": key, "path": str(destination), "hit": False, "bytes": destination.stat().st_size, "etag": remote_etag}))
    return 0


def command_sync(args: argparse.Namespace) -> int:
    key = require_safe_logical_key(args.key)
    destination = workspace_path(key)
    source = require_safe_local_path(args.from_file, must_exist=True) if args.from_file else destination
    if not source.is_file():
        raise StorageError(f"Workspace file does not exist: {source}")
    require_uploadable(source)
    client = make_client()
    remote_key = current_object_key(key)
    remote_head = head_or_none(client, remote_key)
    metadata = load_workspace_metadata(key)
    if remote_head is not None:
        remote_etag = object_etag(remote_head)
        base_etag = str(metadata.get("etag", "")) if metadata and source == destination else ""
        if not args.force and base_etag != remote_etag:
            raise StorageError("Remote object changed or was not opened locally; rerun open, or use --force deliberately")
        client.copy_object(
            Bucket=bucket_name(),
            CopySource={"Bucket": bucket_name(), "Key": remote_key},
            Key=version_object_key(key, remote_etag),
            MetadataDirective="COPY",
        )
    digest = sha256_file(source)
    updated = dt.datetime.now(dt.timezone.utc).isoformat()
    client.upload_file(
        str(source),
        bucket_name(),
        remote_key,
        ExtraArgs={"Metadata": {"sha256": digest, "logical-key": key, "updated-utc": updated}},
    )
    verified = client.head_object(Bucket=bucket_name(), Key=remote_key)
    if int(verified.get("ContentLength", -1)) != source.stat().st_size or object_sha256(verified) != digest:
        raise StorageError(f"Remote verification failed after sync: {key}")
    if source == destination:
        save_workspace_metadata(key, {"etag": object_etag(verified), "sha256": digest, "remote_key": remote_key})
        os.utime(destination, None)
    print(json.dumps({"synced": key, "bytes": source.stat().st_size, "sha256": digest, "versioned_previous": remote_head is not None}))
    return 0


def command_release(args: argparse.Namespace) -> int:
    key = require_safe_logical_key(args.key)
    destination = workspace_path(key)
    if not destination.is_file():
        print(json.dumps({"released": key, "present": False}))
        return 0
    client = make_client()
    head = head_or_none(client, current_object_key(key))
    local_sha = sha256_file(destination)
    if head is None or not object_sha256(head) or object_sha256(head) != local_sha:
        raise StorageError("Refusing to release a file that is not verified against the live Bucket object")
    size = destination.stat().st_size
    destination.unlink()
    workspace_metadata_path(key).unlink(missing_ok=True)
    print(json.dumps({"released": key, "freed_bytes": size, "remote_verified": True}))
    return 0


def default_max_mb() -> int:
    try:
        return max(0, int(os.environ.get("HERMES_STORAGE_CACHE_MAX_MB", "1024")))
    except ValueError as exc:
        raise StorageError("HERMES_STORAGE_CACHE_MAX_MB must be an integer") from exc


def nonnegative_mb(value: str) -> int:
    size = int(value)
    if size < 0:
        raise argparse.ArgumentTypeError("size must be zero or greater")
    return size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Move user artifacts between Hermes and a Railway Bucket")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="verify bucket access without printing credentials")
    status.set_defaults(function=command_status)

    listing = commands.add_parser("list", help="list object metadata")
    listing.add_argument("prefix", nargs="?", default="")
    listing.add_argument("--limit", type=bounded_limit, default=100)
    listing.set_defaults(function=command_list)

    put = commands.add_parser("put", help="upload one safe-root file")
    put.add_argument("local_file")
    put.add_argument("key")
    put.set_defaults(function=command_put)

    get = commands.add_parser("get", help="download one object to the safe root")
    get.add_argument("key")
    get.add_argument("local_file")
    get.add_argument("--force", action="store_true")
    get.set_defaults(function=command_get)

    cache = commands.add_parser("cache", help="fetch an object into the bounded local cache")
    cache.add_argument("key")
    cache.add_argument("--max-mb", type=nonnegative_mb, default=default_max_mb())
    cache.set_defaults(function=command_cache)

    prune = commands.add_parser("prune", help="remove oldest generated cache files only")
    prune.add_argument("--max-mb", type=nonnegative_mb, default=default_max_mb())
    prune.set_defaults(function=command_prune)

    open_object = commands.add_parser("open", help="fetch the latest live object into the managed workspace")
    open_object.add_argument("key")
    open_object.add_argument("--max-mb", type=nonnegative_mb, default=default_workspace_max_mb())
    open_object.set_defaults(function=command_open)

    sync = commands.add_parser("sync", help="upload a workspace file as the latest live object")
    sync.add_argument("key")
    sync.add_argument("--from-file")
    sync.add_argument("--force", action="store_true")
    sync.set_defaults(function=command_sync)

    release = commands.add_parser("release", help="remove a verified local workspace copy only")
    release.add_argument("key")
    release.set_defaults(function=command_release)
    return parser


def bounded_limit(value: str) -> int:
    limit = int(value)
    if limit < 1 or limit > 1000:
        raise argparse.ArgumentTypeError("limit must be between 1 and 1000")
    return limit


def main() -> int:
    try:
        args = build_parser().parse_args()
        return args.function(args)
    except StorageError as exc:
        print(f"hermes-storage: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
