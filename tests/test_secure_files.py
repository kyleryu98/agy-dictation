import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agy_dictation import secure_files as files


@unittest.skipUnless(os.name == "posix", "POSIX private runtime files")
class SecureFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_directory_repair_does_not_chmod_parent(self):
        self.root.chmod(0o755)
        child = self.root / "runtime"
        child.mkdir(mode=0o755)
        files.ensure_private_dir(child)
        self.assertEqual(stat.S_IMODE(child.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)

    def test_directory_symlink_rejected_without_chmod_target(self):
        target = self.root / "target"
        target.mkdir(mode=0o755)
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            files.ensure_private_dir(link)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_chmod_failure_is_not_silently_ignored(self):
        with patch.object(files.os, "fchmod", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                files.ensure_private_dir(self.root)

    def test_file_types_and_links_are_rejected_without_mutation(self):
        target = self.root / "target"
        target.write_text("keep")
        target.chmod(0o600)
        with files.private_directory(self.root) as directory:
            for kind in ("symlink", "hardlink", "fifo", "directory"):
                with self.subTest(kind=kind):
                    path = self.root / kind
                    if kind == "symlink":
                        path.symlink_to(target)
                    elif kind == "hardlink":
                        os.link(target, path)
                    elif kind == "fifo":
                        os.mkfifo(path, 0o600)
                    else:
                        path.mkdir()
                    with self.assertRaises(OSError):
                        files.open_private_file(directory, kind, os.O_RDWR, repair=True)
                    path.rmdir() if kind == "directory" else path.unlink()
        self.assertEqual(target.read_text(), "keep")

    def test_unsafe_permissions_rejected_or_explicitly_repaired(self):
        path = self.root / "log"
        path.write_text("existing")
        path.chmod(0o644)
        with files.private_directory(self.root) as directory:
            with self.assertRaises(PermissionError):
                files.open_private_file(directory, "log")
            fd = files.open_private_file(
                directory,
                "log",
                os.O_WRONLY | os.O_APPEND,
                repair=True,
            )
            os.write(fd, b" more")
            os.close(fd)
        self.assertEqual(path.read_text(), "existing more")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_wrong_owner_rejected(self):
        with patch.object(files.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(PermissionError):
                files.ensure_private_dir(self.root)

    def test_atomic_output_and_predictable_tmp_symlink(self):
        target = self.root / "target"
        target.write_text("keep")
        (self.root / "status.tmp").symlink_to(target)
        with files.private_directory(self.root) as directory:
            files.atomic_write_json(directory, "status.json", {"text": "한글 😀"})
            files.atomic_write_text(directory, "recovery.txt", "recovery")
            self.assertEqual(files.read_json(directory, "status.json"), {"text": "한글 😀"})
        self.assertEqual(target.read_text(), "keep")
        self.assertEqual(stat.S_IMODE((self.root / "status.json").stat().st_mode), 0o600)
        self.assertEqual((self.root / "recovery.txt").read_text(), "recovery")
        self.assertFalse(list(self.root.glob(".exchange-*")))

    def test_atomic_failure_preserves_destination_and_removes_temp(self):
        with files.private_directory(self.root) as directory:
            files.atomic_write_text(directory, "result", "before")
            with patch.object(files.os, "replace", side_effect=OSError("failure")):
                with self.assertRaises(OSError):
                    files.atomic_write_text(directory, "result", "after")
        self.assertEqual((self.root / "result").read_text(), "before")
        self.assertFalse(list(self.root.glob(".exchange-*")))

    def test_atomic_replacement_repairs_legacy_permissions(self):
        path = self.root / "status.json"
        path.write_text("old")
        path.chmod(0o644)
        with files.private_directory(self.root) as directory:
            files.atomic_write_json(directory, path.name, {"state": "idle"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_atomic_destination_symlink_rejected(self):
        target = self.root / "target"
        target.write_text("keep")
        (self.root / "result").symlink_to(target)
        with files.private_directory(self.root) as directory:
            with self.assertRaises(OSError):
                files.atomic_write_text(directory, "result", "overwrite")
        self.assertEqual(target.read_text(), "keep")

    def test_bounded_json_object_and_duplicate_fields(self):
        with files.private_directory(self.root) as directory:
            for content in ("[]", '{"token":1,"token":2}', '"' + "x" * 100 + '"'):
                files.atomic_write_text(directory, "input", content)
                with self.assertRaises(ValueError):
                    files.read_json(directory, "input", 64)

    def test_no_truncation_or_path_traversal(self):
        with files.private_directory(self.root) as directory:
            with self.assertRaises(ValueError):
                files.open_private_file(directory, "file", os.O_TRUNC | os.O_WRONLY)
            for name in ("../escape", ".", "..", ""):
                with self.assertRaises(ValueError):
                    files.atomic_write_json(directory, name, {})

    def test_open_descriptor_survives_directory_rename(self):
        folder = self.root / "runtime"
        with files.private_directory(folder) as directory:
            folder.rename(self.root / "moved")
            folder.symlink_to(self.root, target_is_directory=True)
            files.atomic_write_json(directory, "result", {"ok": True})
        self.assertFalse((self.root / "result").exists())
        self.assertEqual(json.loads((self.root / "moved/result").read_text()), {"ok": True})
