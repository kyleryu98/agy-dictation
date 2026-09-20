import codecs
import fcntl
import hmac
import os
import pty
import re
import select
import subprocess
import shlex
import signal
import termios
import threading
import time
import struct
import uuid
import pyte
from .ipc import RemoteError
from .config import BASE, AGY, EDITOR_BRIDGE, ensure_private_dir
from .secure_files import (
    atomic_write_json,
    exchange_lock,
    private_directory,
    read_json,
    unlink,
    validate_text,
    validate_time,
    validate_token,
)


class BridgeError(RemoteError):
    pass


class CLI:
    def __init__(self, audio_address=None):
        if audio_address is not None and (
            not isinstance(audio_address, str)
            or re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", audio_address) is None
            or not 0 < int(audio_address.rsplit(":", 1)[1]) <= 65535
        ):
            raise BridgeError("protocol_error")
        self.audio_address = audio_address
        self.proc = None
        self.fd = None
        self.text = ""
        self.lock = threading.Lock()
        self.screen = pyte.Screen(120, 40)
        self.stream = pyte.Stream(self.screen)
        self.cancelled = threading.Event()
        self._session = uuid.uuid4().hex
        self._session_used = False
        self._pending = None
        self._state = threading.RLock()
        self._finishing = threading.Lock()
        self._reader = None

    def read(self, proc=None, fd=None):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        proc = self.proc if proc is None else proc
        fd = self.fd if fd is None else fd
        while self.proc is proc and proc.poll() is None:
            try:
                if not select.select([fd], [], [], 0.2)[0]:
                    continue
                data = os.read(fd, 65536)
                if not data:
                    return
                decoded = decoder.decode(data)
                clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", decoded)
                with self.lock:
                    if self.proc is not proc:
                        return
                    self.text = (self.text + clean)[-48000:]
                    self.stream.feed(decoded)
            except OSError:
                return

    def reset(self):
        with self.lock:
            self.text = ""

    def output(self):
        with self.lock:
            return self.text

    def screen_text(self):
        with self.lock:
            return "\n".join(self.screen.display)

    def send(self, chars):
        os.write(self.fd, chars)

    def wait(self, needle, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.cancelled.is_set():
                raise BridgeError("cancelled")
            if self.proc.poll() is not None:
                raise BridgeError("cli_exited")
            t = self.output()
            if needle in t:
                return
            if any(
                x in t.lower()
                for x in (
                    "transcription failed",
                    "microphone permission denied",
                    "voice dictation is not available",
                )
            ):
                raise BridgeError("voice_unavailable")
            time.sleep(0.05)
        raise BridgeError("cli_timeout")

    def start(self, *, reset_cancel=True):
        if self.proc is not None and self.proc.poll() is None:
            return
        if self.fd is not None:
            self.close()
        # An explicit start can recover after reset/failure. begin() already
        # cleared cancellation and must preserve any subsequent cancel request.
        if reset_cancel:
            with self._state:
                self.cancelled.clear()
        ensure_private_dir(BASE)
        folder = BASE / "voice-session"
        ensure_private_dir(folder)
        self.fd, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        except Exception:
            os.close(slave)
            os.close(self.fd)
            self.fd = None
            raise BridgeError("cli_start_failed") from None
        env = os.environ.copy()
        for key in tuple(env):
            if key.startswith(("PYTHON", "DYLD_", "LD_")) or key in {
                "NODE_OPTIONS",
                "NODE_PATH",
                "BASH_ENV",
                "ENV",
                "CDPATH",
            }:
                env.pop(key)
        self._session = uuid.uuid4().hex
        env.update(
            TERM="xterm-256color",
            EDITOR=shlex.quote(str(EDITOR_BRIDGE.absolute())),
            AGY_DICTATION_EXPORT=str((BASE / "transcript.json").absolute()),
            AGY_DICTATION_SESSION=self._session,
        )
        env["VISUAL"] = env["EDITOR"]
        if self.audio_address is not None:
            env["ANTIGRAVITY_MIC"] = self.audio_address
        env["PATH"] = os.pathsep.join(
            entry for entry in env.get("PATH", os.defpath).split(os.pathsep) if os.path.isabs(entry)
        )
        try:
            self.proc = subprocess.Popen(
                [str(AGY.absolute()), "--mode", "plan"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=folder,
                env=env,
                start_new_session=True,
                close_fds=True,
                umask=0o077,
            )
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise BridgeError("cli_start_failed") from None
        finally:
            os.close(slave)
        self.reset()
        self._reader = threading.Thread(target=self.read, args=(self.proc, self.fd), daemon=True)
        self._reader.start()
        try:
            self._wait_ready(folder)
        except Exception:
            self.close()
            raise

    def _wait_ready(self, folder):
        end = time.monotonic() + 45
        while time.monotonic() < end:
            if self.cancelled.is_set():
                raise BridgeError("cancelled")
            text = self.output()
            if "Do you trust the contents of this project?" in text:
                if str(folder) not in text:
                    raise BridgeError("unexpected_project")
                raise BridgeError("trust_required")
            if "for shortcuts" in text:
                return
            if "Terms of Service & Data Use" in text:
                raise BridgeError("terms_required")
            if self.proc.poll() is not None:
                raise BridgeError("cli_start_failed")
            time.sleep(0.1)
        raise BridgeError("login_required")

    def begin(self):
        if self._session_used or (
            self.fd is not None and (self.proc is None or self.proc.poll() is not None)
        ):
            self.close()
        self.cancelled.clear()
        self.start(reset_cancel=False)
        with self._state:
            if self.cancelled.is_set():
                raise BridgeError("cancelled")
            self._session_used = False
            self.reset()
            self.send(b"\x1b[15~")
        self.wait("Recording", 20)

    def finish(self):
        if not self._finishing.acquire(blocking=False):
            raise BridgeError("busy")
        try:
            with private_directory(BASE) as directory:
                return self._finish(directory)
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("protocol_error") from None
        finally:
            self._finishing.release()

    def _finish(self, directory):
        token = uuid.uuid4().hex
        created = time.time()
        with self._state, exchange_lock(directory):
            if self.cancelled.is_set():
                raise BridgeError("cancelled")
            if self._session_used:
                raise BridgeError("recording_required")
            self._session_used = True
            unlink(directory, "transcript.json")
            atomic_write_json(
                directory,
                "transcript-request.json",
                {
                    "token": token,
                    "session": self._session,
                    "time": created,
                },
            )
            self._pending = token
        try:
            with self._state:
                if self.cancelled.is_set():
                    raise BridgeError("cancelled")
                self.reset()
                self.send(b"\x1b[15~")
            # Native editor callback is the completion acknowledgement.
            end = time.monotonic() + 20
            next_request = time.monotonic() + 0.25
            while time.monotonic() < end:
                with self._state:
                    if self.cancelled.is_set():
                        raise BridgeError("cancelled")
                    if self.proc is not None and self.proc.poll() is not None:
                        raise BridgeError("cli_exited")
                    with exchange_lock(directory):
                        try:
                            result = read_json(directory, "transcript.json")
                        except FileNotFoundError:
                            result = None
                        if result is not None:
                            result_token = validate_token(result.get("token"))
                            session = validate_token(result.get("session"))
                            unlink(directory, "transcript.json")
                            if hmac.compare_digest(result_token, token) and hmac.compare_digest(
                                session, self._session
                            ):
                                validate_time(result.get("time"), earliest=created)
                                return validate_text(result.get("text"))
                    if time.monotonic() >= next_request:
                        self.send(b"\x07")
                        next_request = time.monotonic() + 1
                time.sleep(0.05)
            raise BridgeError("transcription_timeout")
        finally:
            with self._state, exchange_lock(directory):
                unlink(directory, "transcript-request.json")
                unlink(directory, "transcript.json")
                self._pending = None

    def cancel(self):
        with self._state:
            self.cancelled.set()
            self._session_used = True
            if self._pending is not None:
                with private_directory(BASE) as directory, exchange_lock(directory):
                    unlink(directory, "transcript-request.json")
                    unlink(directory, "transcript.json")
        if self.proc is not None and self.proc.poll() is None:
            self.send(b"\x1b")

    def close(self):
        try:
            self.cancel()
        except OSError:
            # An already closed PTY must not prevent child/fd cleanup.
            pass
        try:
            if self.proc is not None and self.proc.poll() is None:
                # Popen creates a fresh session, including editor children.
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.proc.wait(timeout=3)
        finally:
            # Stop the old reader before its descriptor can be reused by a new
            # PTY. A reader already inside select has a 200ms bounded wait.
            self.proc = None
            if self._reader is not None:
                self._reader.join(timeout=1)
                self._reader = None
            if self.fd is not None:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
            self.fd = None
