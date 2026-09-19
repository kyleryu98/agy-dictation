import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from agy_dictation import export_prompt as exporter
from agy_dictation import secure_files as files


@unittest.skipUnless(os.name == "posix", "POSIX editor bridge")
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.draft = self.root / "draft.txt"
        self.out = self.root / "transcript.json"
        self.request = self.root / "transcript-request.json"
        self.token = "a" * 32
        self.session = "b" * 32
        self.original = "한글 😀 transcript"
        self.draft.write_text(self.original, encoding="utf-8")
        env = patch.dict(os.environ, AGY_DICTATION_SESSION=self.session)
        env.start()
        self.addCleanup(env.stop)
        self.write_request()

    def write_request(self, **changes):
        request = {"token": self.token, "session": self.session, "time": time.time()}
        request.update(changes)
        with files.private_directory(self.root) as directory:
            files.atomic_write_json(directory, self.request.name, request)

    def test_atomic_export_preserves_unicode_and_clears_only_after_delivery(self):
        exporter.export(self.draft, self.out)
        result = json.loads(self.out.read_text())
        self.assertEqual(result["text"], self.original)
        self.assertEqual(result["token"], self.token)
        self.assertEqual(result["session"], self.session)
        self.assertEqual(self.draft.read_text(), "")
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.request.exists())

    def test_missing_request_does_not_clear_draft(self):
        self.request.unlink()
        with self.assertRaises(FileNotFoundError):
            exporter.export(self.draft, self.out)
        self.assertEqual(self.draft.read_text(), self.original)

    def test_failed_delivery_does_not_clear_draft(self):
        with patch.object(files.os, "replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                exporter.export(self.draft, self.out)
        self.assertEqual(self.draft.read_text(), self.original)
        self.assertFalse(self.request.exists())
        self.assertFalse(list(self.root.glob(".exchange-*")))

    def test_invalid_request_does_not_mutate_draft(self):
        for changes in (
            {"token": ""},
            {"token": [1]},
            {"token": "request-1"},
            {"session": "c" * 32},
            {"time": time.time() - 60},
            {"time": time.time() + 60},
            {"time": True},
        ):
            with self.subTest(changes=changes):
                self.write_request(**changes)
                with self.assertRaises(ValueError):
                    exporter.export(self.draft, self.out)
                self.assertFalse(self.out.exists())
                self.assertEqual(self.draft.read_text(), self.original)

    def test_duplicate_callback_cannot_overwrite_first_result(self):
        exporter.export(self.draft, self.out)
        delivered = self.out.read_bytes()
        with self.assertRaises(FileNotFoundError):
            exporter.export(self.draft, self.out)
        self.assertEqual(self.out.read_bytes(), delivered)

    def test_concurrent_callbacks_deliver_only_once(self):
        def callback():
            try:
                exporter.export(self.draft, self.out)
                return True
            except FileNotFoundError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: callback(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(json.loads(self.out.read_text())["text"], self.original)

    def test_old_session_cannot_claim_new_request(self):
        self.write_request(session="c" * 32)
        with self.assertRaises(ValueError):
            exporter.export(self.draft, self.out)
        self.assertTrue(self.request.exists())
        self.assertEqual(self.draft.read_text(), self.original)

    def test_request_and_draft_symlinks_do_not_touch_target(self):
        target = self.root / "target"
        target.write_text("private data")
        for path in (self.request, self.draft):
            with self.subTest(path=path.name):
                old = path.read_bytes()
                path.unlink()
                path.symlink_to(target)
                with self.assertRaises(OSError):
                    exporter.export(self.draft, self.out)
                self.assertEqual(target.read_text(), "private data")
                path.unlink()
                path.write_bytes(old)
                path.chmod(0o600)

    def test_changed_draft_is_not_cleared(self):
        real_write = exporter.atomic_write_json

        def write(*args):
            real_write(*args)
            self.draft.write_text("new user content")

        with patch.object(exporter, "atomic_write_json", side_effect=write):
            with self.assertRaises(ValueError):
                exporter.export(self.draft, self.out)
        self.assertEqual(self.draft.read_text(), "new user content")

    def test_replaced_draft_path_is_not_truncated(self):
        real_write = exporter.atomic_write_json

        def write(*args):
            real_write(*args)
            self.draft.rename(self.root / "original")
            self.draft.write_text("replacement")

        with patch.object(exporter, "atomic_write_json", side_effect=write):
            exporter.export(self.draft, self.out)
        self.assertEqual(self.draft.read_text(), "replacement")

    def test_main_redacts_decoder_and_parser_failures(self):
        for payload in (b"\xffSECRET_TRANSCRIPT", b"\x1bSECRET_TRANSCRIPT"):
            self.draft.write_bytes(payload)
            stderr = io.StringIO()
            with patch.dict(os.environ, AGY_DICTATION_EXPORT=str(self.out)):
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(exporter.main([str(self.draft)]), 1)
            self.assertNotIn("SECRET_TRANSCRIPT", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertEqual(self.draft.read_bytes(), payload)
