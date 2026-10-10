import importlib.util
import os
from pathlib import Path
import tempfile
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


if __name__ == "__main__":
    unittest.main()
