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
    v = attr(el, "AXValue")
    return str(v) if isinstance(v, str) else None


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
        except (ValueError, OSError):
            raise BridgeError(
                "음성 엔진 요청에 실패했습니다. 권한과 실행 상태를 확인해 주세요."
            ) from None
        finally:
            sock.close()

    def start(self):
        try:
            self.request("ping", 1)
        except Exception:
            subprocess.run(
                ["/usr/bin/open", "-g", "-a", str(ENGINE_APP), "--args", str(ENGINE_BOOTSTRAP)],
                check=True,
            )
            end = time.monotonic() + 90
            while time.monotonic() < end:
                try:
                    self.request("ping", 1)
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                raise BridgeError("AGY Voice Engine의 마이크 권한을 확인해 주세요.")
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


def editable(el):
    role = attr(el, "AXRole", "")
    return role in ("AXTextArea", "AXTextField", "AXComboBox") or bool(
        attr(el, "AXEditable", False)
    )


def focused_input(root):
    direct = attr(root, "AXFocusedUIElement")
    if direct is not None and editable(direct):
        return direct
    window = attr(root, "AXFocusedWindow")
    nested = attr(window, "AXFocusedUIElement")
    if nested is not None and editable(nested):
        return nested
    # WebView applications may report only the containing web area at app level.
    # Accept only an explicitly focused editable descendant, never an arbitrary field.
    stack = [(direct if direct is not None else window, 0)]
    visited = 0
    while stack and visited < 180:
        node, depth = stack.pop()
        visited += 1
        if node is None:
            continue
        if editable(node) and attr(node, "AXFocused", False):
            return node
        if depth < 9:
            stack.extend((child, depth + 1) for child in attr(node, "AXChildren", []))
    return None


def capture_target():
    app = AK.NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = app.processIdentifier()
    root = AX.AXUIElementCreateApplication(pid)
    el = None
    end = time.monotonic() + 0.8
    attempts = 0
    while time.monotonic() < end:
        if AK.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != pid:
            raise BridgeError("앱이 바뀌었습니다. 입력칸에서 다시 눌러 주세요.")
        el = focused_input(root)
        attempts += 1
        if el is not None:
            break
        if attempts == 1 and app.bundleIdentifier() == "com.anthropic.claudefordesktop":
            # Enable Claude/Electron's accessibility tree on demand, not a TCC grant.
            result = AX.AXUIElementSetAttributeValue(root, "AXManualAccessibility", True)
            logging.info("Claude accessibility tree requested result=%s", result)
        if threading.current_thread() is threading.main_thread():
            CF.CFRunLoopRunInMode(CF.kCFRunLoopDefaultMode, 0.08, False)
        else:
            time.sleep(0.08)
    if el is None:
        logging.info("focused input unavailable attempts=%s", attempts)
        raise BridgeError("입력칸을 클릭한 뒤 다시 눌러 주세요.")
    if "Secure" in attr(el, "AXSubrole", ""):
        raise BridgeError("암호 입력란에서는 사용할 수 없습니다.")
    logging.info(
        "target captured role=%s attempts=%s",
        attr(el, "AXRole", ""),
        attempts,
    )
    return {
        "pid": pid,
        "root": root,
        "target": el,
        "before": value(el),
        "range": attr(el, "AXSelectedTextRange"),
    }


def target_unchanged(s):
    front = AK.NSWorkspace.sharedWorkspace().frontmostApplication()
    return (
        front.processIdentifier() == s["pid"]
        and same(focused_input(s["root"]), s["target"])
        and (s["before"] is None or value(s["target"]) == s["before"])
        and (s["range"] is None or same(attr(s["target"], "AXSelectedTextRange"), s["range"]))
    )


def wait_modifiers():
    end = time.monotonic() + 3
    while time.monotonic() < end:
        if not Q.CGEventSourceFlagsState(Q.kCGEventSourceStateCombinedSessionState) & (
            CTRL | OTHER
        ):
            return
        time.sleep(0.02)
    raise BridgeError("보조 키를 놓아 주세요. 전사문을 파일로 보관했습니다.")


def inject(text, s):
    if not target_unchanged(s):
        raise BridgeError("입력 위치나 내용이 바뀌어 자동 입력을 중단했습니다.")
    wait_modifiers()
    if cancel_requested.is_set() or not target_unchanged(s):
        raise BridgeError("입력 위치나 내용이 바뀌어 자동 입력을 중단했습니다.")
    try:
        text = ipc.insertion_text(text)
    except ipc.ProtocolError:
        raise BridgeError("전사문에 입력할 수 없는 제어 문자가 있습니다.") from None
    # Unicode keyboard text insertion: no clipboard, Cmd+V, app activation or Return key.
    if not text.strip():
        raise BridgeError("인식된 음성이 없습니다.")
    for start in range(0, len(text), 16):
        if cancel_requested.is_set():
            raise BridgeError("입력을 취소했습니다. 전사문을 파일로 보관했습니다.")
        if AK.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != s[
            "pid"
        ] or not same(focused_input(s["root"]), s["target"]):
            raise BridgeError("입력 도중 포커스가 바뀌었습니다. 전사문을 파일로 보관했습니다.")
        if Q.CGEventSourceFlagsState(Q.kCGEventSourceStateCombinedSessionState) & (CTRL | OTHER):
            raise BridgeError("입력 도중 보조 키가 눌려 중단했습니다.")
        if "Secure" in attr(s["target"], "AXSubrole", ""):
            raise BridgeError("암호 입력란에서는 사용할 수 없습니다.")
        chunk = text[start : start + 16]
        for down in (True, False):
            event = Q.CGEventCreateKeyboardEvent(None, 0, down)
            Q.CGEventSetFlags(event, 0)
            Q.CGEventKeyboardSetUnicodeString(event, len(chunk.encode("utf-16-le")) // 2, chunk)
            Q.CGEventPost(Q.kCGHIDEventTap, event)
        time.sleep(0.012)
    if s["before"] is not None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            now = value(s["target"])
            if now != s["before"] and text in (now or ""):
                return True
            time.sleep(0.05)
    return False


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
        status("error", "음성 엔진을 시작하지 못했습니다. 권한과 설치 상태를 확인해 주세요.")
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
                    cancel_requested.clear()
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
                    status(
                        "idle",
                        "입력 확인 완료"
                        if verified
                        else "텍스트 입력 전송 완료 · 대상 앱의 텍스트 확인은 제한됨",
                    )
                    beep("Pop")
                    if verified:
                        (BASE / "last-transcript.txt").unlink(missing_ok=True)
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
        if busy:
            status("idle", "녹음을 취소했습니다.")
            threading.Thread(target=backend.cancel, daemon=True).start()
            return
    with mutex:
        if busy or time.monotonic() - last_trigger < 0.4:
            return
        busy = True
        last_trigger = time.monotonic()
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
