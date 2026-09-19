import importlib.util
import queue
import sys
import threading
import unittest
from contextlib import contextmanager, nullcontext
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
            "AppKit": SimpleNamespace(NSPanel=object, NSView=object),
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
            patch.object(b, "wait_for_target", side_effect=b.BridgeError("changed")),
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

    def test_worker_keeps_recovery_without_claiming_success_when_unconfirmed(self):
        class StopWorker(BaseException):
            pass

        for result in (False, b.InputUnconfirmed("확인 불가")):
            with self.subTest(readable_text=result is not False):
                fake_backend = Mock()
                fake_backend.finish.return_value = "synthetic example"
                b.session = {"target": "editor"}
                with (
                    patch.object(b, "backend", fake_backend),
                    patch.object(b, "BASE", MagicMock()) as base,
                    patch.object(b.ops, "get", side_effect=["toggle", StopWorker]),
                    patch.object(b.ops, "task_done"),
                    patch.object(b, "status") as status,
                    patch.object(b, "beep") as beep,
                    patch.object(b, "notify") as notify,
                    patch.object(b, "save_recovery") as save,
                    patch.object(b, "inject", side_effect=[result]),
                ):
                    with self.assertRaises(StopWorker):
                        b.worker()
                save.assert_called_once_with("synthetic example")
                self.assertEqual(status.call_args.args[0], "sent" if result is False else "unconfirmed")
                base.__truediv__.return_value.unlink.assert_not_called()
                beep.assert_not_called()
                notify.assert_not_called()
                self.assertIsNone(b.session)
                self.assertFalse(b.busy)

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
        with (
            patch.object(
                b, "attr", side_effect=lambda el, key, default=None: attrs.get((el, key), default)
            ),
            patch.object(b, "same", side_effect=lambda a, c: a == c),
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
            patch.object(b, "wait_for_target", side_effect=[None, b.BridgeError("changed")]),
            patch.object(b, "wait_modifiers"),
            patch.object(b.Q, "CGEventPost") as post,
        ):
            with self.assertRaises(b.BridgeError):
                b.inject("safe text", {})
            post.assert_not_called()


@unittest.skipIf(sys.platform == "win32", "POSIX service policy")
class FocusCompatibilityTests(unittest.TestCase):
    """Synthetic accessibility trees only: no UI, typing, permission or mic calls."""

    def setUp(self):
        b.cancel_requested.clear()
        self.attrs = {}
        self.clock = 0.0
        for patcher in (
            patch.object(b, "attr", side_effect=self.read_attribute),
            patch.object(b, "same", side_effect=lambda a, c: a == c),
            patch.object(b.AX, "AXUIElementIsAttributeSettable", return_value=(-25205, False)),
            patch.object(b.time, "monotonic", side_effect=lambda: self.clock),
            patch.object(b.time, "sleep", side_effect=self.advance),
            patch.object(b.CF, "CFRunLoopRunInMode", side_effect=lambda *args: self.advance(0.08)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def advance(self, seconds):
        self.clock += seconds

    def read_attribute(self, el, key, default=None):
        return self.attrs.get((el, key), default)

    @contextmanager
    def editor(self, before="A😀B", selected=(1, 2), *, native=True,
               delay=0, terminal_newline=False, switch_after_write=False,
               delivered_suffix=""):
        """Independent UTF-16 editor; every native operation remains mocked."""
        target = {"pid": 123, "root": "app", "target": "editor",
                  "before": before, "range": selected}
        state = {"text": before, "range": selected, "writes": [], "pid": 123}
        payload = [""]
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.side_effect = lambda: state["pid"]

        def read(_):
            if state["writes"] and self.clock - state["at"] < delay:
                return state["old"]
            return state["text"] + ("\n" if terminal_newline and state["writes"] else "")

        def replace(text):
            location, length = state["range"]
            encoded = state["text"].encode("utf-16-le")
            state["old"] = state["text"]
            state["text"] = (
                encoded[:2 * location] + (text + delivered_suffix).encode("utf-16-le")
                + encoded[2 * (location + length):]
            ).decode("utf-16-le")
            state["range"] = (location + len(text.encode("utf-16-le")) // 2, 0)
            state["at"] = self.clock
            state["writes"].append(text)
            if switch_after_write:
                state["pid"] = 456

        def post(_, down):
            if down:
                replace(payload[0])

        with (
            patch.object(b, "AK", kit),
            patch.object(b, "focused_input", return_value="editor"),
            patch.object(b, "value", side_effect=read),
            patch.object(b, "selection_range", side_effect=lambda _: state["range"]),
            patch.object(b, "wait_modifiers"),
            patch.object(b.AX, "AXUIElementIsAttributeSettable", return_value=(0, native)),
            patch.object(b.AX, "AXUIElementSetAttributeValue", return_value=0) as write,
            patch.object(b.Q, "CGEventSourceFlagsState", return_value=0),
            patch.object(b.Q, "CGEventCreateKeyboardEvent", side_effect=lambda a, c, down: down),
            patch.object(b.Q, "CGEventKeyboardSetUnicodeString", side_effect=lambda e, n, t: payload.__setitem__(0, t)),
            patch.object(b.Q, "CGEventPost", side_effect=post) as events,
        ):
            yield target, state, write, events

    def test_keyboard_inserts_entire_sentence_once_preserving_neighbors(self):
        text = "한글과 English 😀 문장 끝까지 한 번에 입력합니다. " * 3
        with self.editor(delay=1.1) as (target, state, write, events):
            self.assertTrue(b.inject(text, target))
        self.assertEqual(state["text"], "A" + text + "B")
        self.assertEqual("".join(state["writes"]), text)
        write.assert_not_called()
        self.assertEqual(events.call_count, 2 * len(state["writes"]))

    def test_complete_delayed_input_is_confirmed_after_foreground_switch(self):
        with self.editor(delay=.2, switch_after_write=True) as (target, state, write, events):
            self.assertTrue(b.inject("complete", target))
        self.assertEqual(state["text"], "AcompleteB")
        write.assert_not_called()
        self.assertEqual(events.call_count, 2)

    def test_final_keyboard_input_is_confirmed_after_foreground_switch(self):
        with self.editor(native=False, switch_after_write=True) as (target, state, write, events):
            self.assertTrue(b.inject("complete", target))
        self.assertEqual(state["text"], "AcompleteB")
        write.assert_not_called()
        self.assertEqual(events.call_count, 2)

    def test_app_added_terminal_newline_does_not_stop_remaining_keyboard_chunks(self):
        text = "한국어 English 😀 마지막 문장까지 입력합니다. " * 2
        with self.editor(native=False, terminal_newline=True) as (target, state, write, events):
            self.assertTrue(b.inject(text, target))
        self.assertEqual(state["text"], "A" + text + "B")
        self.assertEqual("".join(state["writes"]), text)
        write.assert_not_called()
        self.assertGreater(events.call_count, 2)

    def test_stale_text_with_confirmed_caret_does_not_cut_sentence_at_first_chunk(self):
        text = "음성 입력 테스트입니다. 문장 끝까지 확인합니다. 마지막 단어는 해바라기입니다."
        with self.editor(before="", selected=(0, 0)) as (target, state, write, events):
            with patch.object(b, "value", return_value=target["before"]):
                self.assertFalse(b.inject(text, target))
        self.assertEqual(state["text"], text)
        self.assertEqual("".join(state["writes"]), text)
        self.assertEqual(events.call_count, 2 * len(state["writes"]))
        write.assert_not_called()

    def test_partial_text_readback_only_accepts_prefixes_of_our_own_input(self):
        text = "123456789012345 다음 문장 끝까지 확인합니다."
        with self.editor(before="", selected=(0, 0)) as (target, state, write, events):
            with patch.object(b, "value", side_effect=lambda _: text[:5] if state["writes"] else ""):
                self.assertFalse(b.inject(text, target))
        self.assertEqual(state["text"], text)
        self.assertEqual("".join(state["writes"]), text)
        write.assert_not_called()

    def test_stale_readback_requires_matching_caret_before_more_input(self):
        with self.editor() as (target, state, write, events):
            with (
                patch.object(b, "value", return_value=target["before"]),
                patch.object(b, "selection_range", return_value=target["range"]),
            ):
                with self.assertRaises(b.BridgeError):
                    b.inject("x" * 40, target)
        self.assertEqual(state["writes"], ["x" * 16])
        self.assertEqual(events.call_count, 2)
        write.assert_not_called()

    def test_unrelated_text_change_cannot_use_cursor_only_acknowledgement(self):
        with self.editor() as (target, state, write, events):
            with patch.object(b, "value", side_effect=lambda _: "changed baseline" if state["writes"] else target["before"]):
                with self.assertRaises(b.BridgeError):
                    b.inject("x" * 40, target)
        self.assertEqual(state["writes"], ["x" * 16])
        self.assertEqual(events.call_count, 2)
        write.assert_not_called()

    def test_cursor_ack_is_not_available_before_first_write(self):
        with self.editor() as (target, state, write, events):
            with patch.object(b, "value", return_value=""):
                with self.assertRaises(b.BridgeError):
                    b.inject("new text", target)
        self.assertEqual(state["text"], "A😀B")
        events.assert_not_called()
        write.assert_not_called()

    def test_missing_preserved_suffix_is_not_mistaken_for_readback_lag(self):
        with self.editor() as (target, state, write, events):
            with patch.object(b, "value", side_effect=lambda _: state["text"][:-1] if state["writes"] else target["before"]):
                with self.assertRaises(b.BridgeError):
                    b.inject("x" * 40, target)
        self.assertEqual(state["writes"], ["x" * 16])
        self.assertEqual(events.call_count, 2)

    def test_completed_cursor_ack_survives_app_switch_without_false_failure(self):
        with self.editor(switch_after_write=True) as (target, state, write, events):
            with patch.object(b, "value", return_value=target["before"]):
                self.assertFalse(b.inject("complete", target))
        self.assertEqual(state["text"], "AcompleteB")
        self.assertEqual(events.call_count, 2)
        write.assert_not_called()

    def test_advertised_native_setter_is_never_used(self):
        # Chrome advertises this setter and returns zero but can leave text unchanged.
        with self.editor() as (target, state, write, events):
            write.side_effect = None
            write.return_value = 0
            self.assertTrue(b.inject("complete", target))
        self.assertEqual(state["text"], "AcompleteB")
        write.assert_not_called()
        self.assertEqual(events.call_count, 2)

    def test_final_text_mismatch_is_unconfirmed_without_retry(self):
        with self.editor(delivered_suffix="unexpected") as (target, state, write, events):
            with self.assertRaises(b.InputUnconfirmed):
                b.inject("complete", target)
        self.assertEqual(state["text"], "AcompleteunexpectedB")
        write.assert_not_called()
        self.assertEqual(events.call_count, 2)

    def test_rejected_keyboard_input_does_not_delete_selection_or_retry(self):
        with self.editor() as (target, state, write, events):
            events.side_effect = None
            with patch.object(b, "logging") as log:
                with self.assertRaises(b.InputUnconfirmed) as caught:
                    b.inject("PRIVATE CONTENT", target)
        self.assertEqual((state["text"], state["range"]), ("A😀B", (1, 2)))
        write.assert_not_called()
        self.assertEqual(events.call_count, 2)
        self.assertNotIn("PRIVATE CONTENT", str(caught.exception))
        self.assertNotIn("PRIVATE CONTENT", str(log.mock_calls))

    def test_changed_cursor_before_input_preserves_selected_text(self):
        with self.editor() as (target, state, write, events):
            def release_modifiers():
                state["range"] = (0, 1)

            with patch.object(b, "wait_modifiers", side_effect=release_modifiers):
                with self.assertRaises(b.BridgeError):
                    b.inject("new text", target)
        self.assertEqual(state["text"], "A😀B")
        write.assert_not_called()
        events.assert_not_called()

    def test_modifier_or_cancel_before_input_preserves_selected_text(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                with self.editor() as (target, state, write, events):
                    def flags(_):
                        if cancel:
                            b.cancel_requested.set()
                        return b.CTRL

                    with patch.object(b.Q, "CGEventSourceFlagsState", side_effect=flags):
                        with self.assertRaises(b.BridgeError):
                            b.inject("new text", target)
                b.cancel_requested.clear()
                self.assertEqual(state["text"], "A😀B")
                write.assert_not_called()
                events.assert_not_called()

    def test_keyboard_switch_mid_sentence_still_stops_all_remaining_writes(self):
        with self.editor(native=False, switch_after_write=True) as (target, state, write, events):
            with self.assertRaises(b.BridgeError):
                b.inject("x" * 40, target)
        self.assertEqual(state["text"], "A" + "x" * 16 + "B")
        self.assertEqual(state["writes"], ["x" * 16])
        self.assertEqual(events.call_count, 2)
        write.assert_not_called()

    def test_added_newline_does_not_bypass_intermediate_cursor_ack(self):
        with self.editor(native=False, terminal_newline=True) as (target, state, write, events):
            with patch.object(b, "selection_range", return_value=target["range"]):
                with self.assertRaises(b.BridgeError):
                    b.inject("x" * 40, target)
        self.assertEqual(state["writes"], ["x" * 16])
        self.assertEqual(events.call_count, 2)
        write.assert_not_called()

    def test_final_verification_reads_original_field_not_new_foreground(self):
        target = {"target": "original", "before": "complete"}
        with patch.object(b, "value", side_effect=lambda el: "complet" if el == "original" else "complete") as read:
            with self.assertRaises(b.InputUnconfirmed):
                b.wait_for_target(target, final=True, after_input=True)
        self.assertTrue(all(call.args == ("original",) for call in read.call_args_list))

    def test_out_of_bounds_selection_never_attempts_input(self):
        with self.editor(selected=(1, 20)) as (target, state, write, events):
            with self.assertRaises(b.BridgeError):
                b.inject("new text", target)
        self.assertEqual(state["text"], "A😀B")
        write.assert_not_called()
        events.assert_not_called()

    def test_full_range_read_avoids_synthetic_newline_in_ax_value(self):
        self.attrs[("editor", "AXValue")] = "A😀B\n"
        self.attrs[("editor", "AXNumberOfCharacters")] = 4
        with (
            patch.object(b.AX, "AXValueCreate", return_value="range") as create,
            patch.object(b.AX, "AXUIElementCopyParameterizedAttributeValue", return_value=(0, "A😀B")) as read,
        ):
            self.assertEqual(b.value("editor"), "A😀B")
        create.assert_called_once_with(b.AX.kAXValueCFRangeType, (0, 4))
        read.assert_called_once_with("editor", "AXStringForRange", "range", None)

    def test_invalid_or_unsupported_range_read_falls_back_to_ax_value(self):
        self.attrs[("editor", "AXValue")] = "original"
        self.attrs[("editor", "AXNumberOfCharacters")] = 8
        for result in ((-25205, None), (0, "partial"), (0, None)):
            with patch.object(b.AX, "AXUIElementCopyParameterizedAttributeValue", return_value=result):
                self.assertEqual(b.value("editor"), "original")

    def test_changing_character_count_does_not_accept_torn_range_read(self):
        with (
            patch.object(b, "attr", side_effect=[4, 8, "original"]),
            patch.object(b.AX, "AXUIElementCopyParameterizedAttributeValue", return_value=(0, "A😀B")),
        ):
            self.assertEqual(b.value("editor"), "original")

    def test_only_added_terminal_newline_is_tolerated_after_writing(self):
        target = {"pid": 123, "root": "app", "target": "editor", "before": "complete", "range": (8, 0)}
        for actual in ("complet", "complete ", "complete\n\n", "com\nplete"):
            with patch.object(b, "value", return_value=actual):
                self.assertNotEqual(b.target_state(target, final=True), "confirmed")
        with patch.object(b, "value", return_value="complete\n"):
            self.assertEqual(b.target_state(target, final=True), "confirmed")
        # Do not hide the loss of an existing real newline.
        with patch.object(b, "value", return_value="complete"):
            self.assertNotEqual(b.target_state({**target, "before": "complete\n"}, final=True), "confirmed")

    def test_focus_chain_across_web_area_and_frame(self):
        self.attrs.update({
            ("app", "AXFocusedUIElement"): "web",
            ("web", "AXFocusedUIElement"): "frame",
            ("frame", "AXFocusedUIElement"): "editor",
            ("editor", "AXRole"): "AXTextArea",
        })
        self.assertEqual(b.focused_input("app"), "editor")

    def test_focused_static_text_resolves_contenteditable_ancestor(self):
        self.attrs.update({
            ("app", "AXFocusedUIElement"): "text",
            ("text", "AXRole"): "AXStaticText",
            ("text", "AXParent"): "editor",
            ("editor", "AXRole"): "AXGroup",
            ("editor", "AXEditable"): True,
        })
        self.assertEqual(b.focused_input("app"), "editor")

    def test_window_is_searched_even_when_app_reports_web_container(self):
        self.attrs.update({
            ("app", "AXFocusedUIElement"): "web",
            ("app", "AXFocusedWindow"): "window",
            ("window", "AXChildren"): ["group"],
            ("group", "AXChildren"): ["editor"],
            ("editor", "AXRole"): "AXTextArea",
            ("editor", "AXFocused"): True,
        })
        self.assertEqual(b.focused_input("app"), "editor")

    def test_deeply_nested_web_editor(self):
        self.attrs[("app", "AXFocusedUIElement")] = "web0"
        for i in range(15):
            self.attrs[(f"web{i}", "AXChildren")] = [f"web{i + 1}"]
        self.attrs[("web15", "AXFocused")] = True
        self.attrs[("web15", "AXRole")] = "AXTextArea"
        self.assertEqual(b.focused_input("app"), "web15")

    def test_large_page_does_not_starve_shallow_input(self):
        self.attrs.update({
            ("app", "AXFocusedUIElement"): "web",
            ("web", "AXChildren"): ["editor", "article"],
            ("article", "AXChildren"): [f"paragraph{i}" for i in range(600)],
            ("editor", "AXRole"): "AXTextField",
            ("editor", "AXFocused"): True,
        })
        self.assertEqual(b.focused_input("app"), "editor")

    def test_unfocused_input_is_never_used(self):
        self.attrs.update({
            ("app", "AXFocusedUIElement"): "web",
            ("web", "AXChildren"): ["editor"],
            ("editor", "AXRole"): "AXTextArea",
        })
        self.assertIsNone(b.focused_input("app"))

    def test_focus_cycles_are_rejected_and_child_cycles_terminate(self):
        self.attrs.update({
            ("a", "AXFocusedUIElement"): "b",
            ("b", "AXFocusedUIElement"): "a",
            ("a", "AXRole"): "AXTextArea",
        })
        self.assertIsNone(b.focus_owner("a"))
        self.attrs = {("app", "AXFocusedUIElement"): "web", ("web", "AXChildren"): ["web"]}
        self.assertIsNone(b.focused_input("app"))

    def test_system_focus_requires_matching_process(self):
        self.attrs[("system", "AXFocusedUIElement")] = "editor"
        with (
            patch.object(b.AX, "AXUIElementCreateSystemWide", return_value="system"),
            patch.object(b.AX, "AXUIElementGetPid") as get_pid,
        ):
            get_pid.side_effect = [(0, 100), (0, 200)]
            self.assertIsNone(b.system_focus("app"))
            get_pid.side_effect = [(0, 100), (0, 100)]
            self.assertEqual(b.system_focus("app"), "editor")
            get_pid.side_effect = [(1, 100), (0, 100)]
            self.assertIsNone(b.system_focus("app"))

    def test_custom_editor_requires_writable_selection(self):
        self.attrs[("editor", "AXRole")] = "AXGroup"
        self.attrs[("editor", "AXSelectedTextRange")] = "range"
        self.assertFalse(b.editable("editor"))
        with patch.object(b.AX, "AXUIElementIsAttributeSettable", return_value=(0, True)):
            self.assertTrue(b.editable("editor"))

    def test_readonly_disabled_and_explicitly_noneditable_fields_are_rejected(self):
        for key, value in (("AXReadOnly", True), ("AXEnabled", False), ("AXEditable", False)):
            with self.subTest(key=key):
                self.attrs = {("editor", "AXRole"): "AXTextArea", ("editor", key): value}
                self.assertFalse(b.editable("editor"))

    def test_accessibility_request_does_not_depend_on_app_name(self):
        with patch.object(b.AX, "AXUIElementSetAttributeValue", return_value=0) as write:
            b.request_accessibility("app")
        self.assertEqual([call.args for call in write.call_args_list], [
            ("app", "AXManualAccessibility", True),
            ("app", "AXEnhancedUserInterface", True),
        ])

    def test_unsupported_accessibility_request_still_tries_other_capability(self):
        with patch.object(
            b.AX, "AXUIElementSetAttributeValue", side_effect=[RuntimeError(), 0]
        ) as write:
            b.request_accessibility("app")
        self.assertEqual(write.call_count, 2)

    def test_capture_retries_after_accessibility_activation(self):
        app = Mock()
        app.processIdentifier.return_value = 123
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value = app
        self.attrs[("editor", "AXRole")] = "AXTextArea"
        with (
            patch.object(b, "AK", kit),
            patch.object(b.AX, "AXUIElementCreateApplication", return_value="app"),
            patch.object(b, "focused_input", side_effect=[None, "editor", "editor"]),
            patch.object(b, "selection_range", return_value=(0, 0)),
            patch.object(b, "request_accessibility") as activate,
            patch.object(b.CF, "CFRunLoopRunInMode"),
        ):
            self.assertEqual(b.capture_target()["target"], "editor")
        activate.assert_called_once_with("app")
        app.bundleIdentifier.assert_not_called()

    def test_capture_rejects_app_switch_during_resolution(self):
        kit = MagicMock()
        first, second = Mock(), Mock()
        first.processIdentifier.return_value = 123
        second.processIdentifier.return_value = 456
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.side_effect = [
            first, first, second,
        ]
        with (
            patch.object(b, "AK", kit),
            patch.object(b.AX, "AXUIElementCreateApplication", return_value="app"),
            patch.object(b, "focused_input", return_value="editor"),
        ):
            with self.assertRaisesRegex(b.BridgeError, "앱이 바뀌었습니다"):
                b.capture_target()

    def test_selection_missing_malformed_and_not_found_are_rejected(self):
        self.assertIsNone(b.selection_range("editor"))
        self.attrs[("editor", "AXSelectedTextRange")] = "raw"
        with (
            patch.object(b.AX, "AXValueGetType", return_value=b.AX.kAXValueCFRangeType),
            patch.object(b.AX, "AXValueGetValue") as get_range,
        ):
            for location, length in ((-1, 0), (0, -1), (sys.maxsize, 0)):
                get_range.return_value = (True, (location, length))
                self.assertIsNone(b.selection_range("editor"))
            get_range.return_value = (False, (0, 0))
            self.assertIsNone(b.selection_range("editor"))
            get_range.return_value = (True, (2, 3))
            self.assertEqual(b.selection_range("editor"), (2, 3))

    def test_missing_or_moved_cursor_never_posts_input(self):
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 123
        target = {"pid": 123, "root": "app", "target": "editor", "before": "abc", "range": (1, 0)}
        self.attrs[("editor", "AXValue")] = "abc"
        with (
            patch.object(b, "AK", kit),
            patch.object(b, "focused_input", return_value="editor"),
            patch.object(b, "selection_range") as selected,
            patch.object(b.Q, "CGEventPost") as post,
        ):
            for result in (None, (2, 0), (1, 1)):
                selected.return_value = result
                with self.assertRaises(b.BridgeError):
                    b.inject("text", target)
            selected.return_value = (1, 0)
            with self.assertRaises(b.BridgeError):
                b.inject("text", {**target, "range": None})
            post.assert_not_called()

    def test_capture_requires_readable_cursor(self):
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 123
        with (
            patch.object(b, "AK", kit),
            patch.object(b.AX, "AXUIElementCreateApplication", return_value="app"),
            patch.object(b, "focused_input", return_value="editor"),
            patch.object(b, "selection_range", return_value=None),
        ):
            with self.assertRaisesRegex(b.BridgeError, "커서 위치"):
                b.capture_target()

    def test_unicode_selection_replacement_is_acknowledged(self):
        target = {"pid": 123, "root": "app", "target": "editor", "before": "A😀B", "range": (1, 2)}
        snapshots = []

        def unchanged(snapshot):
            snapshots.append(dict(snapshot))
            return True

        with (
            patch.object(b, "wait_for_target", side_effect=lambda snapshot, **kwargs: unchanged(snapshot)),
            patch.object(b, "wait_modifiers"),
            patch.object(b.Q, "CGEventSourceFlagsState", return_value=0),
            patch.object(b.Q, "CGEventPost") as post,
        ):
            self.assertTrue(b.inject("한😀", target))
        self.assertEqual(post.call_count, 2)
        self.assertEqual(snapshots[-1]["before"], "A한😀B")
        self.assertEqual(snapshots[-1]["range"], (4, 0))

    def test_cursor_loss_after_first_chunk_stops_remaining_input(self):
        target = {"pid": 123, "root": "app", "target": "editor", "before": "", "range": (0, 0)}
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 123
        with (
            patch.object(b, "AK", kit),
            patch.object(b, "wait_for_target", side_effect=[
                None, None, None, b.BridgeError("커서 위치 확인 실패")
            ]),
            patch.object(b, "wait_modifiers"),
            patch.object(b, "focused_input", return_value="editor"),
            patch.object(b, "selection_range", return_value=None),
            patch.object(b.Q, "CGEventSourceFlagsState", return_value=0),
            patch.object(b.Q, "CGEventPost") as post,
        ):
            with self.assertRaisesRegex(b.BridgeError, "커서 위치"):
                b.inject("x" * 32, target)
        self.assertEqual(post.call_count, 2)

    def test_delayed_incremental_browser_updates_do_not_truncate_or_duplicate(self):
        target = {"pid": 123, "root": "app", "target": "editor", "before": "leftRIGHT", "range": (4, 0)}
        message = "한글과 English 😀 입력을 앱 전환 직후에도 끝까지 확인합니다."
        pending = {}
        delivered = []
        payload = [""]
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 123

        def view():
            if not pending:
                return target["before"], target["range"]
            elapsed = self.clock - pending["time"]
            chunk = pending["chunk"]
            # First input after activation takes over the old 0.8s deadline.
            ready = 1.1 if len(delivered) == 1 else 0.14
            prefix = chunk if elapsed >= ready else chunk[:len(chunk) // 2]
            encoded = prefix.encode("utf-16-le")
            original = pending["before"].encode("utf-16-le")
            location, length = pending["range"]
            text = (original[:location * 2] + encoded + original[(location + length) * 2:]).decode("utf-16-le")
            selected = None if elapsed < 0.04 else (location + len(encoded) // 2, 0)
            return text, selected

        def post(down):
            if down:
                before, selected = view()
                pending.update(time=self.clock, before=before, range=selected, chunk=payload[0])
                delivered.append(payload[0])

        def focus(_):
            elapsed = self.clock - pending["time"] if pending else 10
            return None if 0.01 < elapsed < 0.03 else "editor"

        with (
            patch.object(b, "AK", kit),
            patch.object(b, "focused_input", side_effect=focus),
            patch.object(b, "selection_range", side_effect=lambda _: view()[1]),
            patch.object(b, "value", side_effect=lambda _: view()[0]),
            patch.object(b, "wait_modifiers"),
            patch.object(b.Q, "CGEventSourceFlagsState", return_value=0),
            patch.object(b.Q, "CGEventCreateKeyboardEvent", side_effect=lambda a, c, down: down),
            patch.object(b.Q, "CGEventKeyboardSetUnicodeString", side_effect=lambda e, n, t: payload.__setitem__(0, t)),
            patch.object(b.Q, "CGEventPost", side_effect=lambda tap, event: post(event)) as send,
        ):
            self.assertTrue(b.inject(message, target))
        self.assertEqual("".join(delivered), message)
        self.assertEqual(view()[0], "left" + message + "RIGHT")
        self.assertEqual(send.call_count, 2 * len(delivered))

    def test_final_full_text_proof_does_not_require_late_caret_ack(self):
        target = {"pid": 123, "root": "app", "target": "editor", "before": "complete", "range": (8, 0)}
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 123
        with (
            patch.object(b, "AK", kit),
            patch.object(b, "focused_input", return_value="editor"),
            patch.object(b, "selection_range", return_value=None),
            patch.object(b, "value", return_value="complete"),
        ):
            self.assertEqual(b.target_state(target), "selection_unavailable")
            b.wait_for_target(target, final=True)
            with self.assertRaises(b.BridgeError):
                b.wait_for_target(target, final=False)

    def test_real_app_or_field_switch_stops_without_waiting(self):
        target = {"pid": 123, "root": "app", "target": "editor", "before": "", "range": (0, 0)}
        for reason in ("app_changed", "target_changed", "secure"):
            started = self.clock
            with patch.object(b, "target_state", return_value=reason):
                with self.assertRaises(b.BridgeError):
                    b.wait_for_target(target)
            self.assertEqual(self.clock, started)

    def test_persistent_missing_ack_never_sends_second_chunk(self):
        target = {"pid": 123, "root": "app", "target": "editor", "before": "", "range": (0, 0)}
        posted = []
        with (
            patch.object(b, "target_state", side_effect=lambda *a, **kw: "selection_unavailable" if posted else "confirmed"),
            patch.object(b, "wait_modifiers"),
            patch.object(b.Q, "CGEventSourceFlagsState", return_value=0),
            patch.object(b.Q, "CGEventPost", side_effect=lambda *args: posted.append(True)),
            patch.object(b, "logging") as log,
        ):
            with self.assertRaises(b.BridgeError):
                b.inject("sensitive synthetic sample" * 2, target)
        self.assertEqual(len(posted), 2)
        self.assertNotIn("sensitive synthetic sample", str(log.mock_calls))

    def test_torn_range_snapshot_does_not_authorize_insertion(self):
        target = {"pid": 123, "root": "app", "target": "editor", "before": "hello", "range": (5, 0)}
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 123
        with (
            patch.object(b, "AK", kit),
            patch.object(b, "focused_input", return_value="editor"),
            patch.object(b, "value", return_value="hello"),
            patch.object(b, "selection_range", side_effect=[(2, 0), (5, 0)]),
        ):
            self.assertEqual(b.target_state(target), "selection_updating")

    def test_capture_waits_for_first_cursor_snapshot_to_settle(self):
        kit = MagicMock()
        kit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 123
        with (
            patch.object(b, "AK", kit),
            patch.object(b.AX, "AXUIElementCreateApplication", return_value="app"),
            patch.object(b, "focused_input", return_value="editor"),
            patch.object(b, "value", return_value=""),
            patch.object(b, "selection_range", side_effect=lambda _: None if self.clock < .16 else (0, 0)),
            patch.object(b, "request_accessibility"),
        ):
            self.assertEqual(b.capture_target()["range"], (0, 0))
        self.assertGreaterEqual(self.clock, .24)

    def test_emoji_payloads_respect_utf16_event_budget(self):
        text = "😀" * 30 + "한글"
        chunks = list(b.unicode_chunks(text))
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 16 for chunk in chunks))

    def test_partial_input_hud_does_not_claim_nothing_was_inserted(self):
        title = presentation("error", "일부만 입력됐을 수 있어요.")[0]
        self.assertEqual(title, "입력을 완료하지 못했어요")

    def test_unconfirmed_input_hud_does_not_claim_success_or_failed_delivery(self):
        title, detail, busy, color = presentation("unconfirmed")
        self.assertEqual(title, "입력 확인이 필요해요")
        self.assertIn("복구문", detail)
        self.assertFalse(busy)
        self.assertEqual(color, "orange")

    def test_sent_with_lagging_readback_is_not_presented_as_failed_input(self):
        title, _, busy, color = presentation("sent")
        self.assertEqual(title, "입력 전송 완료")
        self.assertFalse(busy)
        self.assertEqual(color, "gray")

    def test_text_mismatch_diagnostics_return_codes_without_contents(self):
        for expected, actual, code in (
            ("sample value", "sample\u00a0value", "nbsp"),
            ("sample\n", "sample", "trailing_newline"),
            (" sample ", "sample", "outer_whitespace"),
            ("sample value", "sample", "prefix"),
            ("sample", "different", "different"),
        ):
            self.assertEqual(b.text_relation(expected, actual), code)
