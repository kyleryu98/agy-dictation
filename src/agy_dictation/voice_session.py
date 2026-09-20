"""Coordinate provider dictation and the exact microphone stream it consumes."""

import threading
import time
from .ipc import RemoteError


class VoiceSession:
    def __init__(self, cli, microphone, settings, devices, choose):
        self.cli, self.microphone, self.settings = cli, microphone, settings
        self.devices, self.choose = devices, choose
        self._metadata = None
        self._metadata_until = 0
        self._metadata_lock = threading.Lock()

    def start(self):
        self.cli.start()

    def begin(self):
        preference = self.settings.load()
        with self._metadata_lock:
            self._metadata_until = 0
        device = self.choose(preference.microphone_uid)
        self.microphone.arm(device)
        try:
            self.cli.begin()
            if not self.microphone.wait_ready():
                raise RemoteError("audio_stream_failed")
        except BaseException:
            self.microphone.disarm()
            self.cli.cancel()
            raise

    def finish(self):
        try:
            self.microphone.pause()
            if self.microphone.snapshot()["error"]:
                raise RemoteError("audio_stream_failed")
            return self.cli.finish()
        except BaseException:
            self.cli.cancel()
            raise
        finally:
            self.microphone.disarm()

    def cancel(self):
        self.microphone.disarm()
        self.cli.cancel()

    def close(self):
        self.microphone.disarm()
        self.cli.close()

    def audio_status(self):
        # Device enumeration and preference I/O do not belong on the 30 Hz meter path.
        with self._metadata_lock:
            if self._metadata is None or time.monotonic() >= self._metadata_until:
                devices = self.devices()
                preference = self.settings.load()
                metadata = {"devices": devices}
                try:
                    selected = self.choose(preference.microphone_uid, devices)
                    metadata.update(selected_name=selected["name"], selected_missing=False)
                except RemoteError:
                    metadata.update(selected_name="선택한 마이크 연결 끊김", selected_missing=True)
                self._metadata = metadata
                self._metadata_until = time.monotonic() + 1
            metadata = self._metadata.copy()
        return {**metadata, **self.microphone.snapshot()}
