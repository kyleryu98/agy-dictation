"""Coordinate provider dictation and the exact microphone stream it consumes."""

from .ipc import RemoteError


class VoiceSession:
    def __init__(self, cli, microphone, settings, devices, choose):
        self.cli, self.microphone, self.settings = cli, microphone, settings
        self.devices, self.choose = devices, choose

    def start(self):
        self.cli.start()

    def begin(self):
        preference = self.settings.load()
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
        result = self.microphone.snapshot()
        devices = self.devices()
        preference = self.settings.load()
        result["devices"] = devices
        try:
            selected = self.choose(preference.microphone_uid, devices)
            result["selected_name"] = selected["name"]
            result["selected_missing"] = False
        except RemoteError:
            result["selected_name"] = "선택한 마이크 연결 끊김"
            result["selected_missing"] = True
        return result
