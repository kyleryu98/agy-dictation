"""Microphone-capable helper with bounded same-user IPC."""

import fcntl
import logging
import os
import secrets
import signal
import socket
import stat
import sys
import threading
import AppKit as AK
import CoreFoundation as CF
import AVFoundation as AV
import objc
from ..cli_backend import CLI
from ..config import BASE, LOG, ensure_private_dir
from .. import ipc, secure_files as sf


def execute_command(command, cli, allowed, operation):
    """Dispatch an already authenticated command; control never needs the mic."""
    if command in {"cancel", "shutdown"}:
        cli.cancel()
        return ""
    if command == "ping":
        return ""
    if command != "reset" and not allowed:
        raise ipc.RemoteError("permission_required")
    if not operation.acquire(timeout=0.1):
        raise ipc.RemoteError("busy")
    try:
        text = ""
        if command == "start":
            cli.start()
        elif command == "begin":
            cli.begin()
        elif command == "finish":
            text = cli.finish()
        elif command == "reset":
            cli.close()
        if not isinstance(text, str) or len(text) > ipc.MAX_TRANSCRIPT:
            raise ipc.ProtocolError("Invalid transcript")
        return text
    finally:
        operation.release()


def main():
    ensure_private_dir(BASE)
    ensure_private_dir(LOG)
    with sf.private_directory(BASE) as directory:
        lock_fd = sf.open_private_file(
            directory, "engine.lock", os.O_RDWR | os.O_CREAT, repair=True
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            return
        capability = secrets.token_hex(32)
        sf.atomic_write_json(directory, "engine-auth.json", {"token": capability})
    with sf.private_directory(LOG) as directory:
        log_fd = sf.open_private_file(
            directory, "engine.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, repair=True
        )
    logging.basicConfig(
        stream=os.fdopen(log_fd, "a", encoding="utf-8"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    cli = CLI()
    allowed = [False]
    auth = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio)
    if auth == 3:
        allowed[0] = True
    elif auth == 0:

        def permission(granted):
            allowed[0] = bool(granted)

        AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AV.AVMediaTypeAudio, permission
        )
    shutdown_requested = threading.Event()
    operation = threading.Lock()
    slots = threading.BoundedSemaphore(8)
    socket_identity = [None]

    def handle(conn):
        try:
            with conn, objc.autorelease_pool():
                shutting_down = False
                try:
                    ipc.require_same_user(conn)
                    command = ipc.request_command(ipc.receive(conn, ipc.MAX_REQUEST, 5), capability)
                    text = execute_command(command, cli, allowed[0], operation)
                    shutting_down = command == "shutdown"
                    reply = {"ok": True, "text": text}
                except ipc.RemoteError as error:
                    logging.warning("engine operation failed code=%s", error.code)
                    reply = {"ok": False, "code": error.code}
                except (ipc.ProtocolError, ValueError, OSError) as error:
                    logging.warning("IPC rejected type=%s", type(error).__name__)
                    reply = {"ok": False, "code": "protocol_error"}
                except Exception as error:
                    logging.error("engine operation failed type=%s", type(error).__name__)
                    reply = {"ok": False, "code": "engine_error"}
                try:
                    ipc.send(conn, reply, ipc.MAX_RESPONSE)
                except (ipc.ProtocolError, OSError, UnicodeError):
                    pass
                if shutting_down:
                    shutdown_requested.set()
        finally:
            slots.release()

    def serve():
        # Keep authenticated control available even while permission is pending.
        logging.info("microphone authorized=%s", allowed[0])
        path = BASE / "engine.sock"
        try:
            old = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.geteuid():
                logging.error("engine socket path rejected")
                return
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        os.chmod(path, 0o600)
        info = path.lstat()
        socket_identity[0] = (info.st_dev, info.st_ino)
        server.listen(8)
        while True:
            conn, _ = server.accept()
            if not slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()

    def shutdown(*_):
        cli.close()
        path = BASE / "engine.sock"
        try:
            info = path.lstat()
            if (info.st_dev, info.st_ino) == socket_identity[0]:
                path.unlink()
        except FileNotFoundError:
            pass
        with sf.private_directory(BASE) as directory:
            sf.unlink(directory, "engine-auth.json")
        os.close(lock_fd)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while True:
        CF.CFRunLoopRunInMode(CF.kCFRunLoopDefaultMode, 0.1, False)
        if shutdown_requested.is_set():
            shutdown()


if __name__ == "__main__":
    main()
