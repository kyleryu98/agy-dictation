"""AGY loopback microphone transport and meters derived from the same PCM bytes.

Signed 16-bit little-endian mono PCM at 16 kHz, matching AGY's documented
mic-serve transport. No audio is written to disk or included in status/logs.
"""

import array
import math
import queue
import select
import socket
import sys
import threading
import time

SAMPLE_RATE = 16000
MAX_CHUNK = SAMPLE_RATE * 2


def pcm_levels(data):
    if not data or len(data) % 2 or len(data) > MAX_CHUNK:
        raise ValueError("Invalid PCM buffer")
    samples = array.array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(sample) for sample in samples) / 32768
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    db = max(-80, 20 * math.log10(max(rms, 0.0001)))
    return {"level": max(0, min(1, (db + 60) / 60)), "db": db, "peak": peak}


class MicrophoneStream:
    """One ephemeral loopback listener; capture exists only for an armed session."""

    def __init__(self, capture_factory):
        self.capture_factory = capture_factory
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.drained = threading.Event()
        self.drained.set()
        self.generation = 0
        self.capture = None
        self.connection = None
        self.frames = queue.Queue(maxsize=128)
        self.queued_bytes = 0
        self.armed = False
        self.recording = False
        self.accepting = False
        self.started = 0
        self.error = None
        self.device = None
        self.levels = {"level": 0, "db": -80, "peak": 0}
        self.last_sample = 0
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.server.settimeout(0.2)
        self.address = f"127.0.0.1:{self.server.getsockname()[1]}"
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def arm(self, device):
        self.disarm()
        with self.lock:
            self.device = device
            self.error = None
            self.ready = threading.Event()
            self.armed = True

    def feed(self, data, generation):
        with self.lock:
            if generation != self.generation or not self.accepting or self.error:
                return
            try:
                levels = pcm_levels(data)
                if self.queued_bytes + len(data) > SAMPLE_RATE:
                    raise ValueError("Audio consumer stalled")
                self.frames.put_nowait(data)
                self.queued_bytes += len(data)
                self.drained.clear()
            except (ValueError, queue.Full):
                # Never silently drop words while continuing to show a healthy meter.
                self.error = "audio_stream_failed"
                self.recording = False
                return
            self.levels = levels
            self.last_sample = time.monotonic()

    def fail(self, generation):
        with self.lock:
            if generation == self.generation:
                self.error = "audio_stream_failed"
                self.recording = False

    def _serve(self):
        while not self.closed.is_set():
            try:
                conn, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                if not self.armed or self.connection is not None:
                    conn.close()
                    continue
                self.connection = conn
                self.armed = False
                generation = self.generation
                ready = self.ready
                device = self.device
            try:
                conn.settimeout(0.5)
                capture = self.capture_factory(
                    device,
                    lambda data, generation=generation: self.feed(data, generation),
                    lambda generation=generation: self.fail(generation),
                )
                with self.lock:
                    if generation != self.generation:
                        continue
                    self.capture = capture
                    self.recording = True
                    self.accepting = True
                    self.started = time.monotonic()
                capture.start()
                with self.lock:
                    stale = generation != self.generation
                if stale:
                    capture.stop()
                    continue
                ready.set()
                while not self.closed.is_set():
                    with self.lock:
                        if generation != self.generation or self.error:
                            break
                    try:
                        data = self.frames.get(timeout=0.1)
                    except queue.Empty:
                        # Detect AGY closing the stream even during a silent/pause period.
                        if select.select([conn], [], [], 0)[0]:
                            break
                        continue
                    conn.sendall(data)
                    with self.lock:
                        if generation == self.generation:
                            self.queued_bytes = max(0, self.queued_bytes - len(data))
                            if not self.queued_bytes:
                                self.drained.set()
            except Exception:
                self.fail(generation)
            finally:
                with self.lock:
                    capture = self.capture if generation == self.generation else None
                    if generation == self.generation:
                        if self.recording or self.queued_bytes:
                            self.error = "audio_stream_failed"
                        self.recording = False
                        self.accepting = False
                        self.capture = None
                        self.connection = None
                        self.drained.set()
                if capture is not None:
                    capture.stop()
                conn.close()
                ready.set()

    def wait_ready(self, timeout=8):
        ready = self.ready
        return (
            ready.wait(timeout) and ready is self.ready and self.recording and self.error is None
        )

    def pause(self, drain=True):
        with self.lock:
            capture, self.capture = self.capture, None
            self.recording = False
            if not drain:
                self.accepting = False
        if capture is not None:
            capture.stop()
        with self.lock:
            self.accepting = False
        if drain and not self.drained.wait(0.75):
            with self.lock:
                self.error = "audio_stream_failed"

    def disarm(self):
        self.pause(drain=False)
        with self.lock:
            self.generation += 1
            self.armed = False
            self.ready.set()
            conn, self.connection = self.connection, None
            self.levels = {"level": 0, "db": -80, "peak": 0}
            self.last_sample = 0
            while not self.frames.empty():
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    break
            self.queued_bytes = 0
            self.drained.set()
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def snapshot(self):
        with self.lock:
            age = time.monotonic() - self.last_sample if self.last_sample else None
            if self.recording and (age if age is not None else time.monotonic() - self.started) > 2:
                self.error = "audio_stream_failed"
            stale = age is None or age > 0.5
            return {
                "recording": self.recording,
                "input_uid": self.device["uid"] if self.device else None,
                "input_name": self.device["name"] if self.device else "",
                "level": 0 if stale else self.levels["level"],
                "peak": 0 if stale else self.levels["peak"],
                "db": -80 if stale else self.levels["db"],
                "signal_present": self.recording and not stale,
                "error": self.error,
            }

    def close(self):
        self.closed.set()
        self.disarm()
        self.server.close()
        self.thread.join(timeout=1)
