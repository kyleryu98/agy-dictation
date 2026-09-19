import importlib.util
import queue
import sys
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from agy_dictation import ipc


def load_policy_module(name):
    path = Path(__file__).resolve().parents[1] / "src/agy_dictation/macos" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"agy_dictation.macos._test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if sys.platform != "win32":
    # Never import native input/permission frameworks in policy tests, even on a Mac.
    quartz = MagicMock()
    for name, number in {
        "kCGEventFlagMaskControl": 1,
        "kCGEventFlagMaskCommand": 2,
        "kCGEventFlagMaskAlternate": 4,
        "kCGEventFlagMaskShift": 8,
        "kCGEventKeyDown": 10,
        "kCGEventKeyUp": 11,
    }.items():
        setattr(quartz, name, number)
    with patch.dict(
        sys.modules,
        {
            "ApplicationServices": MagicMock(),
            "AppKit": SimpleNamespace(NSPanel=object),
            "Foundation": SimpleNamespace(NSObject=object),
            "Quartz": quartz,
            "CoreFoundation": MagicMock(),
            "AVFoundation": MagicMock(),
            "objc": SimpleNamespace(autorelease_pool=nullcontext),
            "pynput": SimpleNamespace(keyboard=MagicMock()),
        },
    ):
        hud = load_policy_module("hud")
        presentation = hud.presentation
        with patch.dict(sys.modules, {"agy_dictation.macos.hud": hud}):
            b = load_policy_module("service")
        engine = load_policy_module("engine")


@unittest.skipIf(sys.platform == "win32", "POSIX service policy")
class MacOSTests(unittest.TestCase):
    def setUp(self):
        b.pressed.clear()
        b.session = None
        b.cancel_requested.clear()
        b.busy = False
        b.last_trigger = 0
        b.ops = queue.Queue()

    def test_changed_focus_never_injects(self):
        with (
            patch.object(b, "target_unchanged", return_value=False),
            patch.object(b.Q, "CGEventPost") as post,
        ):
            with self.assertRaises(b.BridgeError):
                b.inject("text", {})
            post.assert_not_called()

    def test_auto_repeat_only_triggers_once_per_physical_key(self):
        with (
            patch.object(b, "trigger") as fire,
            patch.object(b.Q, "CGEventGetIntegerValueField", side_effect=lambda e, _: e),
            patch.object(b.Q, "CGEventGetFlags", return_value=b.CTRL),
        ):
            for code in (50, 42):
                self.assertIsNone(b.intercept(b.Q.kCGEventKeyDown, code))
                self.assertIsNone(b.intercept(b.Q.kCGEventKeyDown, code))
                self.assertIsNone(b.intercept(b.Q.kCGEventKeyUp, code))
            self.assertEqual(fire.call_count, 2)

    def test_cancel_within_toggle_debounce_is_queued(self):
        b.last_trigger = 10
        b.session = {"active": True}
        with patch.object(b.time, "monotonic", return_value=10.2):
            b.trigger("cancel")
        self.assertTrue(b.cancel_requested.is_set())
        self.assertTrue(b.busy)
        self.assertEqual(b.ops.get_nowait(), "cancel")
        self.assertEqual(b.last_trigger, 10)

    def test_busy_cancel_dispatches_without_waiting_for_queue(self):
        b.busy = True
        fake_backend = Mock()
        with (
            patch.object(b, "backend", fake_backend),
            patch.object(b, "status"),
            patch.object(b.threading, "Thread") as thread,
        ):
            b.trigger("cancel")
        self.assertTrue(b.cancel_requested.is_set())
        thread.assert_called_once_with(target=fake_backend.cancel, daemon=True)
        thread.return_value.start.assert_called_once()
        self.assertTrue(b.ops.empty())

    def test_repeated_toggle_is_still_debounced(self):
        b.last_trigger = 10
        with patch.object(b.time, "monotonic", return_value=10.2):
            b.trigger()
        self.assertTrue(b.ops.empty())
        self.assertFalse(b.busy)

    def test_fresh_toggle_clears_previous_cancel_before_queueing(self):
        b.cancel_requested.set()
        with patch.object(b.time, "monotonic", return_value=10):
            b.trigger()
        self.assertFalse(b.cancel_requested.is_set())
        self.assertEqual(b.ops.get_nowait(), "toggle")

    def test_request_preserves_safe_remote_error_and_redacts_bad_responses(self):
        for code in (
            "trust_required",
            "terms_required",
            "permission_required",
            "cancelled",
            "SECRET",
        ):
            with self.subTest(code=code):
                with (
                    patch("socket.socket"),
                    patch.object(b.sf, "private_directory", return_value=nullcontext(1)),
                    patch.object(b.sf, "read_json", return_value={"token": "a" * 64}),
                    patch.object(b.ipc, "require_server_socket"),
                    patch.object(b.ipc, "require_same_user"),
                    patch.object(b.ipc, "send"),
                    patch.object(b.ipc, "receive", return_value={"ok": False, "code": code}),
                ):
                    with self.assertRaises(b.EngineError) as caught:
                        b.CLI().request("start")
                self.assertEqual(
                    caught.exception.code, "engine_unavailable" if code == "SECRET" else code
                )
                self.assertNotIn("SECRET", str(caught.exception))

    def test_permission_failure_is_not_retried_as_missing_engine(self):
        cli = b.CLI()
        with (
            patch.object(
                cli, "request", side_effect=b.EngineError("permission_required")
            ) as request,
            patch.object(b.subprocess, "run") as run,
        ):
            with self.assertRaises(b.EngineError) as caught:
                cli.start()
        self.assertEqual(caught.exception.code, "permission_required")
        request.assert_called_once_with("ping", 1)
        run.assert_not_called()

    def test_worker_displays_safe_startup_diagnosis_only(self):
        class StopWorker(BaseException):
            pass

        for error in (b.EngineError("trust_required"), RuntimeError("SECRET")):
            fake_backend = Mock()
            fake_backend.start.side_effect = error
            with (
                patch.object(b, "backend", fake_backend),
                patch.object(b, "status") as status,
                patch.object(b.ops, "get", side_effect=StopWorker),
            ):
                with self.assertRaises(StopWorker):
                    b.worker()
            detail = status.call_args.args[1]
            self.assertNotIn("SECRET", detail)
            if isinstance(error, b.EngineError):
                self.assertEqual(detail, ipc.ERRORS["trust_required"])

    def test_authenticated_shutdown_client_is_preserved(self):
        cli = b.CLI()
        with patch.object(cli, "request", return_value="") as request:
            self.assertTrue(cli.shutdown())
        request.assert_called_once_with("shutdown", 5)

    def test_engine_controls_work_without_microphone_permission(self):
        cli = Mock()
        operation = threading.Lock()
        # Shutdown/cancel/ping also remain available during another operation.
        with operation:
            for command in ("ping", "cancel", "shutdown"):
                self.assertEqual(engine.execute_command(command, cli, False, operation), "")
        self.assertEqual(cli.cancel.call_count, 2)
        self.assertEqual(engine.execute_command("reset", cli, False, operation), "")
        cli.close.assert_called_once()
        for command in ("start", "begin", "finish"):
            with self.assertRaises(ipc.RemoteError) as caught:
                engine.execute_command(command, cli, False, operation)
            self.assertEqual(caught.exception.code, "permission_required")
        cli.start.assert_not_called()
        cli.begin.assert_not_called()
        cli.finish.assert_not_called()

    def test_engine_releases_operation_lock_after_startup_failure(self):
        cli = Mock()
        cli.start.side_effect = ipc.RemoteError("terms_required")
        operation = threading.Lock()
        with self.assertRaises(ipc.RemoteError) as caught:
            engine.execute_command("start", cli, True, operation)
        self.assertEqual(caught.exception.code, "terms_required")
        self.assertFalse(operation.locked())

    def test_pending_permission_engine_acknowledges_then_shuts_down(self):
        class StopServer(BaseException):
            pass

        token = "a" * 64
        events = []
        cli = Mock()
        cli.close.side_effect = lambda: events.append("closed")
        conn = MagicMock()
        conn.__enter__.return_value = conn
        server = Mock()
        server.accept.side_effect = [(conn, None), StopServer]
        socket_path = MagicMock()
        info = SimpleNamespace(st_dev=1, st_ino=2)
        socket_path.lstat.side_effect = [FileNotFoundError, info, info]
        base = MagicMock()
        base.__truediv__.return_value = socket_path
        files = Mock()
        files.private_directory.side_effect = lambda _: nullcontext(123)
        files.open_private_file.return_value = 456
        scheduled = []

        def thread(*, target, args=(), **kwargs):
            if target.__name__ == "serve":
                scheduled.append(target)
                return Mock()
            return Mock(start=lambda: target(*args))

        def run_loop(*_):
            # Execute both thread bodies synchronously, with no real listener.
            with self.assertRaises(StopServer):
                scheduled.pop()()

        def sent(_, reply, limit):
            self.assertEqual(reply, {"ok": True, "text": ""})
            events.append("acknowledged")

        with (
            patch.object(engine, "BASE", base),
            patch.object(engine, "sf", files),
            patch.object(engine, "ensure_private_dir"),
            patch.object(engine, "CLI", return_value=cli),
            patch.object(engine, "AK", MagicMock()),
            patch.object(engine, "logging", Mock()),
            patch.object(engine.fcntl, "flock"),
            patch.object(engine.os, "fdopen"),
            patch.object(engine.os, "close"),
            patch.object(engine.os, "chmod"),
            patch.object(engine.signal, "signal"),
            patch.object(engine.secrets, "token_hex", return_value=token),
            patch.object(engine.socket, "socket", return_value=server),
            patch.object(engine.threading, "Thread", side_effect=thread),
            patch.object(engine.CF, "CFRunLoopRunInMode", side_effect=run_loop),
            patch.object(
                engine.AV.AVCaptureDevice, "authorizationStatusForMediaType_", return_value=0
            ),
            patch.object(engine.ipc, "require_same_user"),
            patch.object(
                engine.ipc, "receive", return_value={"command": "shutdown", "token": token}
            ),
            patch.object(engine.ipc, "send", side_effect=sent),
        ):
            with self.assertRaises(SystemExit) as exited:
                engine.main()
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(events, ["acknowledged", "closed"])
        cli.cancel.assert_called_once()
        socket_path.unlink.assert_called_once()

    def test_explicitly_focused_webview_input_is_resolved(self):
        attrs = {
            ("root", "AXFocusedUIElement"): "web",
            ("web", "AXRole"): "AXWebArea",
            ("web", "AXChildren"): ["input"],
            ("input", "AXRole"): "AXTextArea",
            ("input", "AXFocused"): True,
        }
        with patch.object(
            b, "attr", side_effect=lambda el, key, default=None: attrs.get((el, key), default)
        ):
            self.assertEqual(b.focused_input("root"), "input")
            attrs[("input", "AXFocused")] = False
            self.assertIsNone(b.focused_input("root"))

    def test_processing_hud_is_indeterminate(self):
        self.assertTrue(presentation("transcribing")[2])
        self.assertFalse(presentation("recording")[2])
        self.assertIsNone(presentation("idle", "준비됨"))

    def test_target_is_rechecked_after_waiting_for_modifiers(self):
        with (
            patch.object(b, "target_unchanged", side_effect=[True, False]),
            patch.object(b, "wait_modifiers"),
            patch.object(b.Q, "CGEventPost") as post,
        ):
            with self.assertRaises(b.BridgeError):
                b.inject("safe text", {})
            post.assert_not_called()
