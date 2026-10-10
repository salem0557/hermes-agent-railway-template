import importlib.util
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "hermes_storage.py"
SPEC = importlib.util.spec_from_file_location("hermes_storage", MODULE_PATH)
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)


class HermesStorageSafetyTests(unittest.TestCase):
    def test_object_keys_reject_escape_paths(self):
        for value in ("", "/absolute", "../escape", "folder/../escape", ".", "folder/"):
            with self.subTest(value=value), self.assertRaises(storage.StorageError):
                storage.normalize_key(value)

    def test_object_keys_normalize_windows_separators(self):
        self.assertEqual(storage.normalize_key("reports\\daily.json"), "reports/daily.json")

    def test_local_paths_must_stay_under_safe_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "safe"
            root.mkdir()
            old_value = os.environ.get("HERMES_WRITE_SAFE_ROOT")
            os.environ["HERMES_WRITE_SAFE_ROOT"] = str(root)
            try:
                accepted = storage.require_safe_local_path(str(root / "artifact.txt"))
                self.assertEqual(accepted, (root / "artifact.txt").resolve())
                with self.assertRaises(storage.StorageError):
                    storage.require_safe_local_path(str(Path(temporary) / "outside.txt"))
            finally:
                if old_value is None:
                    os.environ.pop("HERMES_WRITE_SAFE_ROOT", None)
                else:
                    os.environ["HERMES_WRITE_SAFE_ROOT"] = old_value

    def test_sensitive_files_are_rejected(self):
        for path in (
            Path("/data/.hermes/.env"),
            Path("/data/.hermes/state.sqlite"),
            Path("/data/.hermes/sessions/run.json"),
            Path("/data/.hermes/private.key"),
        ):
            with self.subTest(path=path), self.assertRaises(storage.StorageError):
                storage.require_uploadable(path)

    def test_sensitive_live_object_keys_are_rejected(self):
        for key in ("sessions/run.json", "private.key", "state.db", "tokens/access.json"):
            with self.subTest(key=key), self.assertRaises(storage.StorageError):
                storage.require_safe_logical_key(key)

    def test_prune_only_removes_oldest_cache_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "safe"
            cache = root / "cache" / "objects"
            cache.mkdir(parents=True)
            old_file = cache / "old.bin"
            new_file = cache / "new.bin"
            old_file.write_bytes(b"a" * 700_000)
            new_file.write_bytes(b"b" * 700_000)
            os.utime(old_file, (1, 1))
            os.utime(new_file, (2, 2))
            old_root = os.environ.get("HERMES_WRITE_SAFE_ROOT")
            old_cache = os.environ.get("HERMES_STORAGE_CACHE_DIR")
            os.environ["HERMES_WRITE_SAFE_ROOT"] = str(root)
            os.environ["HERMES_STORAGE_CACHE_DIR"] = str(root / "cache")
            try:
                removed, remaining = storage.prune_cache(1)
                self.assertEqual(removed, 1)
                self.assertFalse(old_file.exists())
                self.assertTrue(new_file.exists())
                self.assertEqual(remaining, 700_000)
            finally:
                if old_root is None:
                    os.environ.pop("HERMES_WRITE_SAFE_ROOT", None)
                else:
                    os.environ["HERMES_WRITE_SAFE_ROOT"] = old_root
                if old_cache is None:
                    os.environ.pop("HERMES_STORAGE_CACHE_DIR", None)
                else:
                    os.environ["HERMES_STORAGE_CACHE_DIR"] = old_cache

    def test_prune_preserves_the_object_needed_by_current_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "safe"
            cache = root / "cache" / "objects"
            cache.mkdir(parents=True)
            current = cache / "current.bin"
            old_file = cache / "old.bin"
            current.write_bytes(b"a" * 1_200_000)
            old_file.write_bytes(b"b" * 100_000)
            os.utime(old_file, (1, 1))
            os.utime(current, (2, 2))
            with mock.patch.dict(os.environ, {
                "HERMES_WRITE_SAFE_ROOT": str(root),
                "HERMES_STORAGE_CACHE_DIR": str(root / "cache"),
            }):
                removed, remaining = storage.prune_cache(1, protected={current})
            self.assertEqual(removed, 1)
            self.assertTrue(current.exists())
            self.assertFalse(old_file.exists())
            self.assertEqual(remaining, 1_200_000)

    def test_download_replaces_destination_only_after_success(self):
        class FailingClient:
            def download_file(self, _bucket, _key, filename):
                Path(filename).write_bytes(b"partial")
                raise OSError("connection lost")

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.bin"
            with mock.patch.object(storage, "make_client", return_value=FailingClient()), \
                 mock.patch.object(storage, "bucket_name", return_value="bucket"):
                with self.assertRaises(OSError):
                    storage.download("artifact.bin", destination, force=False)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])


class MissingObject(Exception):
    response = {"Error": {"Code": "404"}}


class MemoryS3Client:
    def __init__(self):
        self.objects = {}

    def _put(self, key, body, metadata=None):
        etag = hashlib.md5(body, usedforsecurity=False).hexdigest()
        self.objects[key] = {"body": body, "metadata": metadata or {}, "etag": etag}

    def head_object(self, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise MissingObject()
        item = self.objects[Key]
        return {"ContentLength": len(item["body"]), "Metadata": item["metadata"], "ETag": f'"{item["etag"]}"'}

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        del bucket
        metadata = (ExtraArgs or {}).get("Metadata", {})
        self._put(key, Path(filename).read_bytes(), metadata)

    def download_file(self, bucket, key, filename):
        del bucket
        Path(filename).write_bytes(self.objects[key]["body"])

    def copy_object(self, Bucket, CopySource, Key, MetadataDirective):
        del Bucket, MetadataDirective
        source = self.objects[CopySource["Key"]]
        self._put(Key, source["body"], dict(source["metadata"]))


class HermesStorageWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "safe"
        self.root.mkdir()
        self.client = MemoryS3Client()
        self.environment = mock.patch.dict(os.environ, {
            "HERMES_WRITE_SAFE_ROOT": str(self.root),
            "HERMES_STORAGE_CACHE_DIR": str(self.root / "storage-cache"),
        })
        self.environment.start()
        self.client_patch = mock.patch.object(storage, "make_client", return_value=self.client)
        self.bucket_patch = mock.patch.object(storage, "bucket_name", return_value="bucket")
        self.client_patch.start()
        self.bucket_patch.start()

    def tearDown(self):
        self.bucket_patch.stop()
        self.client_patch.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def test_live_file_round_trip_versions_previous_and_releases_verified_copy(self):
        source = self.root / "report.txt"
        source.write_text("version one", encoding="utf-8")
        storage.command_sync(SimpleNamespace(key="reports/report.txt", from_file=str(source), force=True))

        opened = storage.workspace_path("reports/report.txt")
        storage.command_open(SimpleNamespace(key="reports/report.txt", max_mb=10))
        self.assertEqual(opened.read_text(encoding="utf-8"), "version one")

        opened.write_text("version two", encoding="utf-8")
        storage.command_sync(SimpleNamespace(key="reports/report.txt", from_file=None, force=False))
        versions = [key for key in self.client.objects if key.startswith("workspace/versions/reports/report.txt/")]
        self.assertEqual(len(versions), 1)
        self.assertEqual(self.client.objects[versions[0]]["body"], b"version one")

        storage.command_release(SimpleNamespace(key="reports/report.txt"))
        self.assertFalse(opened.exists())
        self.assertEqual(self.client.objects["workspace/current/reports/report.txt"]["body"], b"version two")

    def test_sync_rejects_remote_change_after_open(self):
        source = self.root / "report.txt"
        source.write_text("base", encoding="utf-8")
        storage.command_sync(SimpleNamespace(key="reports/report.txt", from_file=str(source), force=True))
        storage.command_open(SimpleNamespace(key="reports/report.txt", max_mb=10))
        opened = storage.workspace_path("reports/report.txt")
        opened.write_text("local edit", encoding="utf-8")
        self.client._put("workspace/current/reports/report.txt", b"remote edit", {"sha256": hashlib.sha256(b"remote edit").hexdigest()})

        with self.assertRaises(storage.StorageError):
            storage.command_sync(SimpleNamespace(key="reports/report.txt", from_file=None, force=False))

    def test_release_rejects_unsynced_local_change(self):
        source = self.root / "report.txt"
        source.write_text("base", encoding="utf-8")
        storage.command_sync(SimpleNamespace(key="reports/report.txt", from_file=str(source), force=True))
        storage.command_open(SimpleNamespace(key="reports/report.txt", max_mb=10))
        opened = storage.workspace_path("reports/report.txt")
        opened.write_text("unsynced", encoding="utf-8")

        with self.assertRaises(storage.StorageError):
            storage.command_release(SimpleNamespace(key="reports/report.txt"))
        self.assertTrue(opened.exists())

    def test_open_does_not_replace_unmanaged_local_file(self):
        source = self.root / "report.txt"
        source.write_text("remote", encoding="utf-8")
        storage.command_sync(SimpleNamespace(key="reports/report.txt", from_file=str(source), force=True))
        unmanaged = storage.workspace_path("reports/report.txt")
        unmanaged.parent.mkdir(parents=True)
        unmanaged.write_text("local only", encoding="utf-8")

        with self.assertRaises(storage.StorageError):
            storage.command_open(SimpleNamespace(key="reports/report.txt", max_mb=10))
        self.assertEqual(unmanaged.read_text(encoding="utf-8"), "local only")


if __name__ == "__main__":
    unittest.main()
