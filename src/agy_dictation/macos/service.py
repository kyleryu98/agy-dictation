#!/usr/bin/env python3
"""Global dictation using AGY's documented interactive CLI; no GUI/clipboard automation."""

import fcntl
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
import ApplicationServices as AX
import AppKit as AK
import Quartz as Q
import CoreFoundation as CF
import objc
from pynput import keyboard
from .hud import DictationHUD
from ..config import BASE, LOG, ENGINE_APP, ENGINE_BOOTSTRAP, ensure_private_dir
from .. import ipc, secure_files as sf

CTRL = Q.kCGEventFlagMaskControl
OTHER = Q.kCGEventFlagMaskCommand | Q.kCGEventFlagMaskAlternate | Q.kCGEventFlagMaskShift
HOTKEY_CODES = {50, 42}
ops = queue.Queue()
busy = False
session = None
pressed = set()
mutex = threading.Lock()
last_trigger = 0.0
status_view = ("starting", "")
backend = None
cancel_requested = threading.Event()


class BridgeError(Exception):
    pass


class InputUnconfirmed(BridgeError):
    """All input was sent, but the original field did not acknowledge its text."""


class EngineError(BridgeError):
    def __init__(self, code):
        error = ipc.RemoteError(code)
        self.code = error.code
        super().__init__(str(error))


def attr(el, name, default=None):
    if el is None:
        return default
    try:
        err, v = AX.AXUIElementCopyAttributeValue(el, name, None)
        return v if err == 0 and v is not None else default
    except Exception:
        return default


def same(a, b):
    return a is not None and b is not None and bool(CF.CFEqual(a, b))


def value(el):
    # Some web editors expose a rendered AXValue with an extra paragraph newline.
    # Prefer the actual text range when supported, using UTF-16 just like the caret.
    count = attr(el, "AXNumberOfCharacters")
    if type(count) is int and 0 <= count <= ipc.MAX_TRANSCRIPT * 2:
        try:
            selected = AX.AXValueCreate(AX.kAXValueCFRangeType, (0, count))
            err, text = AX.AXUIElementCopyParameterizedAttributeValue(
                el, "AXStringForRange", selected, None
            )
            if (err == 0 and isinstance(text, str)
                    and len(text.encode("utf-16-le")) // 2 == count
                    and attr(el, "AXNumberOfCharacters") == count):
                return str(text)
        except Exception:
            pass
    v = attr(el, "AXValue")
    return str(v) if isinstance(v, str) else None


def selection_range(el):
    """Return a verified UTF-16 selection, never treat missing AX data as a caret."""
    raw = attr(el, "AXSelectedTextRange")
    if raw is None:
        return None
    try:
        if AX.AXValueGetType(raw) != AX.kAXValueCFRangeType:
            return None
        ok, selected = AX.AXValueGetValue(raw, AX.kAXValueCFRangeType, None)
        if not ok:
            return None
        location, length = selected
        if (
            not isinstance(location, int) or not isinstance(length, int)
            or location < 0 or length < 0 or location + length >= sys.maxsize
        ):
            return None
        return location, length
    except Exception:
        return None


def status(kind, detail=""):
    global status_view
    status_view = (kind, detail)
    with sf.private_directory(BASE) as directory:
        sf.atomic_write_json(
            directory, "status.json", {"state": kind, "detail": detail, "time": time.time()}
        )
    logging.info("%s %s", kind, detail)


def notify(message):
    subprocess.Popen(
        [
            "/usr/bin/osascript",
            "-e",
            'on run argv\ndisplay notification (item 1 of argv) with title "AGY 음성입력"\nend run',
            message,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def beep(name):
    subprocess.Popen(
        ["/usr/bin/afplay", f"/System/Library/Sounds/{name}.aiff"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class CLI:
    def request(self, command, timeout=45):
        import socket

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            if command not in ipc.COMMANDS:
                raise ipc.ProtocolError("Unknown command")
            with sf.private_directory(BASE) as directory:
                token = ipc.validate_token(
                    sf.read_json(directory, "engine-auth.json", 1024).get("token")
                )
            ipc.require_server_socket(BASE / "engine.sock")
            sock.connect(str(BASE / "engine.sock"))
            ipc.require_same_user(sock)
            ipc.send(sock, {"command": command, "token": token}, ipc.MAX_REQUEST)
            return ipc.response_text(ipc.receive(sock, ipc.MAX_RESPONSE, timeout))
        except ipc.RemoteError as error:
            raise EngineError(error.code) from None
        except (ValueError, OSError):
            raise EngineError("engine_unavailable") from None
        finally:
            sock.close()

    def start(self):
        try:
            self.request("ping", 1)
        except EngineError as error:
            if error.code != "engine_unavailable":
                raise
            subprocess.run(
                ["/usr/bin/open", "-g", "-a", str(ENGINE_APP), "--args", str(ENGINE_BOOTSTRAP)],
                check=True,
            )
            end = time.monotonic() + 90
            while time.monotonic() < end:
                try:
                    self.request("ping", 1)
                    break
                except EngineError as error:
                    if error.code != "engine_unavailable":
                        raise
                    time.sleep(0.2)
            else:
                raise EngineError("engine_unavailable")
        self.request("start")

    def begin(self):
        self.start()
        self.request("begin")

    def finish(self):
        return self.request("finish")

    def cancel(self):
        try:
            self.request("cancel", 3)
        except Exception:
            pass

    def close(self):
        try:
            self.request("reset", 5)
        except Exception:
            pass

    def shutdown(self):
        try:
            self.request("shutdown", 5)
            return True
        except BridgeError:
            return False


def editable(el):
    if el is None or attr(el, "AXEnabled", True) is False or attr(el, "AXReadOnly", False):
        return False
    role = attr(el, "AXRole", "")
    explicit = attr(el, "AXEditable")
    if explicit is not None:
        return bool(explicit)
    if role in ("AXTextArea", "AXTextField", "AXComboBox"):
        return True
    # Custom editors can expose selection insertion without a standard text role.
    # A readable AXValue/selection alone also occurs on non-editable page text.
    try:
        err, writable = AX.AXUIElementIsAttributeSettable(el, "AXSelectedText", None)
        return err == 0 and bool(writable)
    except Exception:
        return False


def focus_owner(node):
    """Resolve an explicitly focused object, including its editable text ancestor."""
    seen = []
    for _ in range(24):
        if node is None or any(same(node, previous) for previous in seen):
            return None
        seen.append(node)
        child = attr(node, "AXFocusedUIElement")
        if child is None or same(child, node):
            break
        node = child
    else:
        return None
    # Web editors sometimes focus a static-text leaf inside a contenteditable.
    # Only walk ancestors of an explicit focus, never a random text descendant.
    for _ in range(24):
        if node is None or attr(node, "AXRole", "") in ("AXWindow", "AXApplication"):
            return None
        if attr(node, "AXEnabled", True) is False or attr(node, "AXReadOnly", False):
            return None
        if editable(node):
            return node
        parent = attr(node, "AXParent")
        if parent is None or same(parent, node):
            return None
        node = parent
    return None


def system_focus(root):
    """Use system focus only when it belongs to the captured application."""
    try:
        node = attr(AX.AXUIElementCreateSystemWide(), "AXFocusedUIElement")
        if node is None:
            return None
        root_err, root_pid = AX.AXUIElementGetPid(root, None)
        node_err, node_pid = AX.AXUIElementGetPid(node, None)
        if root_err == node_err == 0 and root_pid == node_pid:
            return node
    except Exception:
        pass
    return None


def focused_input(root):
    direct = attr(root, "AXFocusedUIElement")
    window = attr(root, "AXFocusedWindow")
    nested = attr(window, "AXFocusedUIElement")
    for node in (direct, nested):
        owner = focus_owner(node)
        if owner is not None:
            return owner
    global_focus = system_focus(root)
    owner = focus_owner(global_focus)
    if owner is not None:
        return owner
    # Search all focus roots, including the window when app focus is a web area.
    # Breadth-first traversal avoids spending the entire budget on one page branch.
    pending = deque((node, 0) for node in (direct, nested, global_focus, window))
    seen = set()
    deadline = time.monotonic() + 0.25
    while pending and len(seen) < 512 and time.monotonic() < deadline:
        node, depth = pending.popleft()
        if node is None:
            continue
        # PyObjC CF wrappers are hashable and compare by the underlying CF value.
        if node in seen:
            continue
        seen.add(node)
        focused = attr(node, "AXFocusedUIElement")
        candidate = focused if focused is not None else (
            node if attr(node, "AXFocused", False) else None
        )
        owner = focus_owner(candidate)
        if owner is not None:
            return owner
        if depth < 32:
            pending.extend((child, depth + 1) for child in attr(node, "AXChildren", []))
    return None


def request_accessibility(root):
    """Ask the target to expose its AX tree; never grant TCC or activate its window."""
    # Chromium uses EnhancedUserInterface; Electron also supports ManualAccessibility.
    # Try capabilities on any app instead of maintaining a bundle-ID allowlist.
    for name in ("AXManualAccessibility", "AXEnhancedUserInterface"):
        try:
            result = AX.AXUIElementSetAttributeValue(root, name, True)
        except Exception:
            result = "unsupported"
        logging.info("accessibility tree request attribute=%s result=%s", name, result)


def capture_target():
    app = AK.NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = app.processIdentifier()
    root = AX.AXUIElementCreateApplication(pid)
    end = time.monotonic() + 2.5
    previous = None
    attempts = 0
    while time.monotonic() < end:
        if cancel_requested.is_set():
            raise BridgeError("녹음을 취소했습니다.")
        if AK.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != pid:
            raise BridgeError("앱이 바뀌었습니다. 입력칸에서 다시 눌러 주세요.")
        el = focused_input(root)
        attempts += 1
        if el is not None and "Secure" in attr(el, "AXSubrole", ""):
            raise BridgeError("암호 입력란에서는 사용할 수 없습니다.")
        selected = selection_range(el) if el is not None else None
        before = value(el) if el is not None else None
        valid = selected is not None and selected == selection_range(el) and (
            before is None or sum(selected) <= len(before.encode("utf-16-le")) // 2
        )
        if valid:
            candidate = {"pid": pid, "root": root, "target": el,
                         "before": before, "range": selected}
            if (previous is not None and same(el, previous["target"])
                    and before == previous["before"] and selected == previous["range"]):
                if AK.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != pid:
                    raise BridgeError("앱이 바뀌었습니다. 입력칸에서 다시 눌러 주세요.")
                logging.info("target captured role=%s attempts=%s", attr(el, "AXRole", ""), attempts)
                return candidate
            previous = candidate
        else:
            previous = None
        if attempts == 1 and not valid:
            request_accessibility(root)
        if threading.current_thread() is threading.main_thread():
            CF.CFRunLoopRunInMode(CF.kCFRunLoopDefaultMode, 0.08, False)
        else:
            time.sleep(0.08)
    logging.info("focused input unavailable attempts=%s", attempts)
    raise BridgeError("입력칸의 커서 위치를 확인할 수 없어요. 커서를 놓고 다시 눌러 주세요.")


def text_relation(expected, actual):
    """Classify mismatches without exposing either text in diagnostics."""
    if not isinstance(actual, str):
        return "unavailable"
    if expected == actual:
        return "exact"
    if expected.replace("\u00a0", " ") == actual.replace("\u00a0", " "):
        return "nbsp"
    if expected.rstrip("\n") == actual.rstrip("\n"):
        return "trailing_newline"
    if expected.strip() == actual.strip():
        return "outer_whitespace"
    if expected.startswith(actual):
        return "prefix"
    return "different"


def delivered_text_matches(expected, actual):
    # Accept only one ADDED terminal paragraph separator. Never trim a missing
    # character, space, or an existing newline; prefix matches are not delivery.
    return isinstance(actual, str) and (actual == expected or actual == expected + "\n")


def pending_input_snapshot(s, actual):
    """Accept stale readback only when it is a known stage of our own insertion."""
    if not isinstance(actual, str) or not s.get("input_written"):
        return False
    baseline = s.get("input_baseline")
    if baseline is None:
        return False
    if actual == baseline:
        return True
    prefix, suffix = s["input_context"]
    if not actual.startswith(prefix) or not actual.endswith(suffix):
        return False
    end = len(actual) - len(suffix) if suffix else len(actual)
    if end < len(prefix):
        return False
    return s["input_sent"].startswith(actual[len(prefix):end])


def target_state(s, *, final=False):
    """Read AX as an asynchronous snapshot, never log values or selection offsets."""
    if final and s["before"] is not None:
        # No more writes follow. Verify the captured element even after the user
        # switches apps; checking the new foreground field would misreport success.
        actual = value(s["target"])
        if delivered_text_matches(s["before"], actual):
            return "confirmed"
        selected = selection_range(s["target"])
        if (selected is not None and selected == s.get("range")
                and selected == selection_range(s["target"])
                and pending_input_snapshot(s, actual)):
            return "cursor_confirmed"
        return "text_pending_" + text_relation(s["before"], actual)
    if s.get("range") is None:
        return "selection_unavailable"
    if AK.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != s["pid"]:
        return "app_changed"
    focused = focused_input(s["root"])
    if focused is None:
        return "focus_unavailable"
    if not same(focused, s["target"]):
        return "target_changed"
    if "Secure" in attr(focused, "AXSubrole", ""):
        return "secure"
    selected = selection_range(focused)
    actual = value(focused)
    after = selection_range(focused)
    if AK.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != s["pid"]:
        return "app_changed"
    last_focus = focused_input(s["root"])
    if last_focus is None:
        return "focus_unavailable"
    if not same(last_focus, focused):
        return "target_changed"
    if selected is None or after is None:
        return "selection_unavailable"
    if selected != after:
        return "selection_updating"
    if s["before"] is not None and actual is None:
        return "text_unavailable"
    matches = actual == s["before"]
    if s.get("input_written") and s["before"] is not None:
        matches = delivered_text_matches(s["before"], actual)
    if s["before"] is not None and not matches:
        if selected == s["range"] and pending_input_snapshot(s, actual):
            return "cursor_confirmed"
        return "text_pending_" + text_relation(s["before"], actual)
    if selected != s["range"]:
        return "selection_pending"
    return "confirmed"


def target_unchanged(s):
    return target_state(s) == "confirmed"


def wait_for_target(s, *, final=False, after_input=False, timeout=2.0):
    """Pause writes through stale/intermediate AX data; never resend a chunk."""
    deadline = time.monotonic() + timeout
    stable_since = None
    stable_state = None
    pending = None
    state = "unavailable"
    while time.monotonic() < deadline:
        if cancel_requested.is_set():
            raise BridgeError("입력을 취소했습니다. 전사문을 파일로 보관했습니다.")
        state = target_state(s, final=final)
        if state in {"app_changed", "target_changed", "secure"}:
            logging.info("input verification stopped reason=%s", state)
            detail = "일부만 입력됐을 수 있어요." if after_input else "자동 입력하지 않았어요."
            raise BridgeError("입력 위치가 바뀌어 중단했습니다. " + detail)
        if state in {"confirmed", "cursor_confirmed"}:
            if stable_since is None or stable_state != state:
                stable_since = time.monotonic()
                stable_state = state
            elif time.monotonic() - stable_since >= 0.04:
                if state == "confirmed" or not final:
                    if pending is not None:
                        logging.info("input verification recovered reason=%s", pending)
                    return state == "confirmed"
        else:
            stable_since = None
            pending = state
        time.sleep(0.02)
    if (state == "cursor_confirmed" and stable_since is not None
            and time.monotonic() - stable_since >= 0.04):
        logging.info("input sent; caret confirmed, text readback pending")
        return False
    selected = selection_range(s["target"])
    logging.info(
        "input verification timeout reason=%s final=%s caret_matches=%s known_snapshot=%s",
        state, final, selected is not None and selected == s.get("range"),
        pending_input_snapshot(s, value(s["target"])),
    )
    if final and after_input:
        raise InputUnconfirmed("입력은 보냈지만 결과를 확인하지 못했어요. 전체 문장은 복구 파일에 보관했습니다.")
    if after_input:
        raise BridgeError("입력 결과 확인이 지연돼 중단했습니다. 일부만 입력됐을 수 있어요. 전체 문장은 복구 파일에 보관했습니다.")
    raise BridgeError("커서 위치를 확인할 수 없어 자동 입력하지 않았어요. 전체 문장은 복구 파일에 보관했습니다.")


def unicode_chunks(text):
    """Keep each keyboard payload within 16 UTF-16 units, including emoji."""
    chunk = ""
    units = 0
    for character in text:
        width = len(character.encode("utf-16-le")) // 2
        if units + width > 16:
            yield chunk
            chunk, units = "", 0
        chunk += character
        units += width
    if chunk:
        yield chunk


def wait_modifiers():
    end = time.monotonic() + 3
    while time.monotonic() < end:
        if not Q.CGEventSourceFlagsState(Q.kCGEventSourceStateCombinedSessionState) & (
            CTRL | OTHER
        ):
            return
        time.sleep(0.02)
    raise BridgeError("보조 키를 놓아 주세요. 전사문을 파일로 보관했습니다.")


def insertion_result(s, text):
    encoded = text.encode("utf-16-le")
    location, length = s["range"]
    expected = None
    if s["before"] is not None:
        original = s["before"].encode("utf-16-le")
        if location < 0 or length < 0 or (location + length) * 2 > len(original):
            raise BridgeError("커서 위치를 확인할 수 없어 자동 입력을 중단했습니다.")
        try:
            expected = (original[:location * 2] + encoded + original[(location + length) * 2:]).decode("utf-16-le")
        except UnicodeDecodeError:
            raise BridgeError("커서 위치를 확인할 수 없어 자동 입력을 중단했습니다.") from None
    result = {**s, "range": (location + len(encoded) // 2, 0),
              "before": expected, "input_written": True}
    if "input_baseline" not in s:
        result["input_baseline"] = s["before"]
        if s["before"] is not None:
            result["input_context"] = (
                original[:location * 2].decode("utf-16-le"),
                original[(location + length) * 2:].decode("utf-16-le"),
            )
    result["input_sent"] = s.get("input_sent", "") + text
    return result


def check_input_keys():
    flags = Q.CGEventSourceFlagsState(Q.kCGEventSourceStateCombinedSessionState)
    if cancel_requested.is_set():
        raise BridgeError("입력을 취소했습니다. 전사문을 파일로 보관했습니다.")
    if flags & (CTRL | OTHER):
        raise BridgeError("입력 도중 보조 키가 눌려 중단했습니다.")


def inject(text, s):
    wait_for_target(s)
    wait_modifiers()
    wait_for_target(s)
    try:
        text = ipc.insertion_text(text)
    except ipc.ProtocolError:
        raise BridgeError("전사문에 입력할 수 없는 제어 문자가 있습니다.") from None
    # Direct insertion only: no clipboard, Cmd+V, app activation or Return key.
    if not text.strip():
        raise BridgeError("인식된 음성이 없습니다.")
    # Chromium can advertise writable AXSelectedText and return success without
    # changing text. Keep one verified Unicode event path instead of probing writes.
    current = dict(s)
    chunks = list(unicode_chunks(text))
    for index, chunk in enumerate(chunks):
        wait_for_target(current, after_input=index > 0)
        check_input_keys()
        if "Secure" in attr(s["target"], "AXSubrole", ""):
            raise BridgeError("암호 입력란에서는 사용할 수 없습니다.")
        encoded = chunk.encode("utf-16-le")
        confirmed = insertion_result(current, chunk)
        for down in (True, False):
            event = Q.CGEventCreateKeyboardEvent(None, 0, down)
            Q.CGEventSetFlags(event, 0)
            Q.CGEventKeyboardSetUnicodeString(event, len(encoded) // 2, chunk)
            Q.CGEventPost(Q.kCGHIDEventTap, event)
        # Wait for acknowledgement before sending another chunk. Never keep typing
        # after a same-field cursor move, inaccessible selection or rejected event.
        verified = wait_for_target(confirmed, final=index == len(chunks) - 1, after_input=True)
        current = confirmed
    return current["before"] is not None and verified is not False


def save_recovery(text):
    with sf.private_directory(BASE) as directory:
        sf.atomic_write_text(directory, "last-transcript.txt", text)


def worker():
    global busy, session
    try:
        backend.start()
        status("idle", "준비됨 · Ctrl + ₩")
    except Exception as e:
        backend.close()
        logging.error("engine startup failed type=%s", type(e).__name__)
        status(
            "error",
            str(e)
            if isinstance(e, BridgeError)
            else "음성 엔진을 시작하지 못했습니다. 권한과 설치 상태를 확인해 주세요.",
        )
    while True:
        action = ops.get()
        with objc.autorelease_pool():
            try:
                if action == "cancel":
                    backend.cancel()
                    session = None
                    status("idle", "녹음을 취소했습니다.")
                elif session is None:
                    target = capture_target()
                    if cancel_requested.is_set():
                        raise EngineError("cancelled")
                    status("connecting")
                    backend.begin()
                    if cancel_requested.is_set():
                        backend.cancel()
                        session = None
                        status("idle", "녹음을 취소했습니다.")
                    else:
                        session = target
                        status("recording")
                        beep("Tink")
                else:
                    status("transcribing")
                    text = backend.finish()
                    s = session
                    session = None
                    if cancel_requested.is_set():
                        status("idle", "녹음을 취소했습니다.")
                        continue
                    if not text.strip():
                        raise BridgeError("인식된 음성이 없습니다.")
                    text = ipc.insertion_text(text)
                    save_recovery(text)
                    status("inserting")
                    verified = inject(text, s)
                    if verified:
                        status("idle", "입력 확인 완료")
                        beep("Pop")
                        (BASE / "last-transcript.txt").unlink(missing_ok=True)
                    else:
                        status("sent", "입력 전송 완료 · 전사문 보관됨")
            except Exception as e:
                if not isinstance(e, BridgeError):
                    logging.error("bridge failed type=%s", type(e).__name__)
                backend.cancel()
                session = None
                msg = (
                    str(e)
                    if isinstance(e, BridgeError)
                    else "내부 오류가 발생했습니다. 로그를 확인해 주세요."
                )
                if cancel_requested.is_set():
                    status("idle", "녹음을 취소했습니다.")
                elif isinstance(e, InputUnconfirmed):
                    status("unconfirmed", msg)
                else:
                    status("error", msg)
                    notify(msg)
                if "CLI" in msg:
                    backend.close()
            finally:
                with mutex:
                    busy = False
                ops.task_done()


def trigger(action="toggle"):
    global busy, last_trigger
    if action == "cancel":
        cancel_requested.set()
        with mutex:
            was_busy = busy
            if not was_busy:
                busy = True
                ops.put(action)
        if was_busy:
            status("idle", "녹음을 취소했습니다.")
            threading.Thread(target=backend.cancel, daemon=True).start()
        return
    with mutex:
        if busy or time.monotonic() - last_trigger < 0.4:
            return
        busy = True
        last_trigger = time.monotonic()
        if session is None:
            cancel_requested.clear()
    ops.put(action)


def intercept(kind, event):
    code = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
    flags = Q.CGEventGetFlags(event)
    match = code in HOTKEY_CODES and flags & CTRL and not flags & OTHER
    cancel = (
        code == 53
        and (session is not None or status_view[0] in ("connecting", "transcribing", "inserting"))
        and not flags & (CTRL | OTHER)
    )
    if kind == Q.kCGEventKeyDown and (match or cancel):
        if code not in pressed:
            pressed.add(code)
            trigger("cancel" if cancel else "toggle")
        return None
    if kind == Q.kCGEventKeyUp and code in pressed:
        pressed.discard(code)
        return None
    return event


def main():
    global backend
    ensure_private_dir(BASE)
    ensure_private_dir(LOG)
    with sf.private_directory(BASE) as directory:
        lock_fd = sf.open_private_file(
            directory, "service.lock", os.O_RDWR | os.O_CREAT, repair=True
        )
    handle = os.fdopen(lock_fd, "r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    with sf.private_directory(LOG) as directory:
        log_fd = sf.open_private_file(
            directory, "bridge.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, repair=True
        )
    logging.basicConfig(
        stream=os.fdopen(log_fd, "a", encoding="utf-8"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not AX.AXIsProcessTrusted():
        status("permission_required", "Python 제어 권한이 필요합니다.")
        return 0
    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    AK.NSWorkspace.sharedWorkspace()
    hud = DictationHUD(lambda: trigger("cancel"))
    backend = CLI()
    status("starting", "AGY CLI 준비 중")

    def shutdown(*_):
        backend.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=worker, daemon=True).start()
    listener = keyboard.Listener(darwin_intercept=intercept)
    listener.start()
    listener.wait()
    previous = None
    previous_hud = None
    try:
        while listener.is_alive():
            if status_view != previous:
                kind, detail = status_view
                hud.update(kind, detail)
                previous = status_view
            hud.tick()
            hud_state = (hud.visible, hud.kind)
            if hud_state != previous_hud:
                with sf.private_directory(BASE) as directory:
                    sf.atomic_write_json(
                        directory,
                        "hud-status.json",
                        {
                            "visible": hud.visible,
                            "state": hud.kind,
                            "key_window": bool(hud.panel.isKeyWindow()),
                            "time": time.time(),
                        },
                    )
                previous_hud = hud_state
            CF.CFRunLoopRunInMode(CF.kCFRunLoopDefaultMode, 0.1, False)
    finally:
        backend.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
