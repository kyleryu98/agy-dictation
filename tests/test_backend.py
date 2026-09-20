import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agy_dictation import secure_files as files
from agy_dictation.export_prompt import export

if sys.platform != "win32":
    from agy_dictation import cli_backend as backend


@unittest.skipIf(sys.platform == "win32", "PTY backend is not yet ported to Windows")
class BackendTests(unittest.TestCase):
    def test_microphone_stream_is_limited_to_a_valid_loopback_port(self):
        for address in ("example.invalid:1234", "0.0.0.0:1234", "127.0.0.1:0", "127.0.0.1:99999"):
            with self.subTest(address=address), self.assertRaises(backend.BridgeError):
                backend.CLI(audio_address=address)
        self.assertEqual(backend.CLI(audio_address="127.0.0.1:1234").audio_address, "127.0.0.1:1234")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(backend, "BASE", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.cli = backend.CLI()
        self.sent = []
        self.elapsed = 0
        clock = patch.object(backend.time, "monotonic", side_effect=lambda: self.elapsed)
        sleeper = patch.object(backend.time, "sleep", side_effect=self.advance)
        clock.start()
        sleeper.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleeper.stop)

    def advance(self, seconds):
        self.elapsed += seconds

    def respond(self, **changes):
        request = json.loads((self.root / "transcript-request.json").read_text())
        result = {**request, "text": "완료", "time": time.time()}
        result.update(changes)
        with files.private_directory(self.root) as directory:
            files.atomic_write_json(directory, "transcript.json", result)

    def finish(self, callback):
        def send(keys):
            self.sent.append(keys)
            if keys == b"\x07":
                callback()

        with patch.object(self.cli, "send", side_effect=send):
            return self.cli.finish()

    def assert_clean(self):
        self.assertFalse((self.root / "transcript-request.json").exists())
        self.assertFalse((self.root / "transcript.json").exists())

    def test_native_editor_callback_finishes_without_prompt_submission(self):
        def respond():
            self.assertEqual((self.root / "transcript-request.json").stat().st_mode & 0o777, 0o600)
            self.respond()

        self.assertEqual(self.finish(respond), "완료")
        self.assertEqual(self.sent, [b"\x1b[15~", b"\x07"])
        self.assertNotIn(b"\r", self.sent)
        self.assert_clean()

    def test_finish_with_real_exporter_uses_one_shot_exchange(self):
        draft = self.root / "draft"
        draft.write_text("완료 😀")

        def respond():
            with patch.dict(os.environ, AGY_DICTATION_SESSION=self.cli._session):
                export(draft, self.root / "transcript.json")

        self.assertEqual(self.finish(respond), "완료 😀")
        self.assertEqual(draft.read_text(), "")
        self.assert_clean()

    def test_processing_cancel_does_not_wait_or_create_request(self):
        self.cli.cancelled.set()
        with patch.object(self.cli, "send") as send:
            with self.assertRaisesRegex(backend.BridgeError, "취소"):
                self.cli.finish()
        send.assert_not_called()
        self.assert_clean()

    def test_stale_result_token_is_not_accepted(self):
        responses = iter((lambda: self.respond(token="c" * 32), self.respond))
        self.assertEqual(self.finish(lambda: next(responses)()), "완료")
        self.assertEqual(self.sent.count(b"\x07"), 2)
        self.assert_clean()

    def test_wrong_session_is_not_accepted(self):
        responses = iter((lambda: self.respond(session="c" * 32), self.respond))
        self.assertEqual(self.finish(lambda: next(responses)()), "완료")
        self.assertEqual(self.sent.count(b"\x07"), 2)

    def test_invalid_response_fails_closed_and_is_cleaned(self):
        for changes in (
            {"text": []},
            {"text": "secret\x00content"},
            {"token": []},
            {"time": True},
            {"time": time.time() - 60},
            {"time": time.time() + 60},
        ):
            with self.subTest(changes=changes):
                self.cli = backend.CLI()
                with self.assertRaises(backend.BridgeError) as error:
                    self.finish(lambda: self.respond(**changes))
                self.assertNotIn("secret", str(error.exception))
                self.assert_clean()

    def test_malformed_or_oversize_response_is_redacted_and_cleaned(self):
        for value in ("[]", '{"text":"SECRET', '"' + "x" * (files.MAX_PAYLOAD + 1)):

            def write():
                output = self.root / "transcript.json"
                output.write_text(value)
                output.chmod(0o600)

            self.cli = backend.CLI()
            with self.assertRaises(backend.BridgeError) as error:
                self.finish(write)
            self.assertNotIn("SECRET", str(error.exception))
            self.assert_clean()

    def test_response_symlink_cannot_exfiltrate_target(self):
        target = self.root / "private"
        target.write_text("SECRET")

        def respond():
            (self.root / "transcript.json").symlink_to(target)

        with self.assertRaises(backend.BridgeError):
            self.finish(respond)
        self.assertEqual(target.read_text(), "SECRET")
        self.assert_clean()

    def test_request_symlink_is_rejected_without_clobber(self):
        target = self.root / "private"
        target.write_text("keep")
        (self.root / "transcript-request.json").symlink_to(target)
        with patch.object(self.cli, "send") as send:
            with self.assertRaises(backend.BridgeError):
                self.cli.finish()
        send.assert_not_called()
        self.assertEqual(target.read_text(), "keep")

    def test_timeout_removes_request_and_response(self):
        with self.assertRaisesRegex(backend.BridgeError, "20초"):
            self.finish(lambda: None)
        self.assert_clean()

    def test_cancel_invalidates_pending_request_immediately(self):
        def cancel():
            self.cli.cancel()
            self.assert_clean()

        with self.assertRaisesRegex(backend.BridgeError, "취소"):
            self.finish(cancel)
        self.assert_clean()

    def test_duplicate_finish_requires_new_recording(self):
        self.finish(self.respond)
        with patch.object(self.cli, "send") as send:
            with self.assertRaises(backend.BridgeError):
                self.cli.finish()
        send.assert_not_called()
        self.assert_clean()

    def test_concurrent_finish_is_rejected(self):
        self.cli._finishing.acquire()
        try:
            with self.assertRaises(backend.BridgeError):
                self.cli.finish()
        finally:
            self.cli._finishing.release()

    def test_begin_restarts_used_session(self):
        self.cli._session_used = True
        with patch.object(self.cli, "close") as close, patch.object(self.cli, "start"):
            with patch.object(self.cli, "send"), patch.object(self.cli, "wait"):
                self.cli.begin()
        close.assert_called_once()
        self.assertFalse(self.cli.cancelled.is_set())
        self.assertFalse(self.cli._session_used)

    def test_cancel_during_start_is_not_cleared_by_begin(self):
        with patch.object(self.cli, "start", side_effect=lambda **_: self.cli.cancel()):
            with patch.object(self.cli, "send") as send:
                with self.assertRaisesRegex(backend.BridgeError, "취소"):
                    self.cli.begin()
        send.assert_not_called()
        self.assertTrue(self.cli.cancelled.is_set())

    def test_cancel_during_recording_wait_is_not_ignored(self):
        self.cli.cancelled.set()
        with self.assertRaisesRegex(backend.BridgeError, "취소"):
            self.cli.wait("Recording", 20)

    def test_cancel_during_startup_stops_at_next_poll(self):
        self.cli.proc = Mock()
        self.cli.proc.poll.return_value = None

        def cancel_after_first_poll(seconds):
            self.advance(seconds)
            self.cli.cancelled.set()

        with patch.object(backend.time, "sleep", side_effect=cancel_after_first_poll):
            with self.assertRaises(backend.BridgeError) as caught:
                self.cli._wait_ready(self.root)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertLessEqual(self.elapsed, 0.1)

    def test_cancel_beats_ready_banner(self):
        self.cli.cancelled.set()
        with patch.object(self.cli, "output", return_value="for shortcuts") as output:
            with self.assertRaises(backend.BridgeError) as caught:
                self.cli._wait_ready(self.root)
        self.assertEqual(caught.exception.code, "cancelled")
        output.assert_not_called()

    def test_first_run_errors_keep_safe_codes_after_cleanup(self):
        for output, code in (
            (f"Do you trust the contents of this project? {self.root}", "trust_required"),
            ("Do you trust the contents of this project? SECRET", "unexpected_project"),
            ("Terms of Service & Data Use SECRET", "terms_required"),
            ("SECRET", "login_required"),
        ):
            with self.subTest(code=code):
                self.cli = backend.CLI()
                self.cli.proc = Mock()
                self.cli.proc.poll.return_value = None
                with patch.object(self.cli, "output", return_value=output):
                    with self.assertRaises(backend.BridgeError) as caught:
                        self.cli._wait_ready(self.root)
                with patch.object(self.cli, "send"), patch.object(backend.os, "killpg"):
                    self.cli.close()
                self.assertTrue(self.cli.cancelled.is_set())
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("SECRET", str(caught.exception))

    def test_spawn_has_private_umask_absolute_editor_and_sanitized_env(self):
        # reset()/failed-start cleanup marks the previous session cancelled;
        # an explicit subsequent start must still reach readiness.
        self.cli.cancelled.set()
        process = Mock()
        process.poll.return_value = None
        bridge = self.root / "space dir/editor"
        close_fd = os.close
        with patch.object(backend, "EDITOR_BRIDGE", bridge):
            with patch.dict(
                os.environ,
                PYTHONPATH="untrusted",
                NODE_OPTIONS="untrusted",
                DYLD_INSERT_LIBRARIES="untrusted",
                LD_PRELOAD="untrusted",
                PATH=".:/usr/bin::relative:/bin",
            ):
                with patch.object(backend.pty, "openpty", return_value=(91, 92)):
                    with (
                        patch.object(backend.fcntl, "ioctl"),
                        patch.object(
                            backend.os,
                            "close",
                            side_effect=lambda fd: None if fd in (91, 92) else close_fd(fd),
                        ),
                    ):
                        with patch.object(
                            backend.subprocess, "Popen", return_value=process
                        ) as popen:
                            with patch.object(backend.threading, "Thread"):
                                with patch.object(self.cli, "output", return_value="for shortcuts"):
                                    self.cli.start()
        self.assertFalse(self.cli.cancelled.is_set())
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["umask"], 0o077)
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["env"]["EDITOR"], backend.shlex.quote(str(bridge)))
        self.assertEqual(kwargs["env"]["AGY_DICTATION_SESSION"], self.cli._session)
        self.assertEqual(kwargs["env"]["PATH"], "/usr/bin:/bin")
        for key in ("PYTHONPATH", "NODE_OPTIONS", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD"):
            self.assertNotIn(key, kwargs["env"])

    def test_spawn_failure_closes_both_pty_descriptors(self):
        close_fd = os.close
        with patch.object(backend.pty, "openpty", return_value=(91, 92)):
            with (
                patch.object(backend.fcntl, "ioctl"),
                patch.object(
                    backend.os,
                    "close",
                    side_effect=lambda fd: None if fd in (91, 92) else close_fd(fd),
                ) as close,
            ):
                with patch.object(backend.subprocess, "Popen", side_effect=OSError("SECRET")):
                    with self.assertRaises(backend.BridgeError) as error:
                        self.cli.start()
        self.assertNotIn("SECRET", str(error.exception))
        self.assertTrue({91, 92}.issubset({call.args[0] for call in close.call_args_list}))
        self.assertIsNone(self.cli.fd)

    def test_startup_handshake_failure_closes_child(self):
        process = Mock()
        process.poll.return_value = None
        close_fd = os.close
        with patch.object(backend.pty, "openpty", return_value=(91, 92)):
            with (
                patch.object(backend.fcntl, "ioctl"),
                patch.object(
                    backend.os,
                    "close",
                    side_effect=lambda fd: None if fd in (91, 92) else close_fd(fd),
                ),
            ):
                with patch.object(backend.subprocess, "Popen", return_value=process):
                    with patch.object(backend.threading, "Thread"):
                        with patch.object(self.cli, "close") as close:
                            with patch.object(
                                self.cli, "_wait_ready", side_effect=backend.BridgeError
                            ):
                                with self.assertRaises(backend.BridgeError):
                                    self.cli.start()
        close.assert_called_once()

    def test_close_kills_child_group_and_reaps_after_timeout(self):
        process = Mock(pid=123456)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("agy", 3), 0]
        self.cli.proc = process
        self.cli.fd = 91
        with patch.object(self.cli, "send", side_effect=OSError):
            with patch.object(backend.os, "killpg") as killpg, patch.object(backend.os, "close"):
                self.cli.close()
        self.assertEqual(killpg.call_count, 2)
        self.assertEqual(process.wait.call_count, 2)
        self.assertIsNone(self.cli.proc)
        self.assertIsNone(self.cli.fd)
