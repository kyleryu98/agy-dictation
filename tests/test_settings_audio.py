import json
import math
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agy_dictation import ipc
from agy_dictation.settings import Preferences, Settings
from agy_dictation.audio_stream import MicrophoneStream, pcm_levels
from agy_dictation.voice_session import VoiceSession
from agy_dictation.macos.controls import AudioStatus, InputFeedback, LaunchAtLogin, level_text


class SettingsTests(unittest.TestCase):
    def test_saved_preferences_reload_privately(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "runtime"
            settings = Settings(base)
            self.assertEqual(settings.load(), Preferences())
            saved = settings.change(
                shortcut="ctrl_option_d", sounds=False, microphone_uid="test-uid"
            )
            self.assertEqual(Settings(base).load(), saved)
            self.assertEqual((base / "settings.json").stat().st_mode & 0o777, 0o600)

    def test_invalid_preferences_do_not_replace_valid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(Path(folder) / "runtime")
            settings.change(sounds=False)
            for changes in ({"shortcut": "unknown"}, {"sounds": 1}, {"microphone_uid": ""}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    settings.change(**changes)
                self.assertEqual(Settings(settings.base).load(), Preferences(sounds=False))

    def test_setting_symlink_does_not_overwrite_target(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            target = base / "keep"
            target.write_text("preserved")
            (base / "settings.json").symlink_to(target)
            with self.assertRaises(OSError):
                Settings(base).change(sounds=False)
            self.assertEqual(target.read_text(), "preserved")

    def test_login_control_targets_only_its_own_service(self):
        login = LaunchAtLogin("com.example.dictation")
        results = iter(
            (
                Mock(stdout='"com.example.dictation" => true'),
                Mock(stdout=""),
                Mock(stdout='"com.example.dictation" => false'),
            )
        )
        with (
            patch.object(Path, "is_file", return_value=True),
            patch(
                "agy_dictation.macos.controls.subprocess.run",
                side_effect=lambda *a, **k: next(results),
            ) as run,
        ):
            login.set_enabled(True)
        self.assertEqual(run.call_args_list[1].args[0], ["/bin/launchctl", "enable", login.target])


class AudioStreamTests(unittest.TestCase):
    def test_meter_measures_silence_signal_and_clipping(self):
        self.assertEqual(pcm_levels(bytes(320))["level"], 0)
        data = struct.pack("<320h", *(int(16384 * math.sin(i / 10)) for i in range(320)))
        value = pcm_levels(data)
        self.assertAlmostEqual(value["db"], -9, delta=0.3)
        self.assertGreater(value["level"], 0.8)
        self.assertEqual(pcm_levels(struct.pack("<h", -32768))["peak"], 1)

    def test_meter_rejects_misaligned_and_unbounded_buffers(self):
        for data in (b"", b"x", bytes(32002)):
            with self.assertRaises(ValueError):
                pcm_levels(data)

    def test_disarmed_connections_cannot_open_microphone(self):
        factory = Mock()
        stream = MicrophoneStream(factory)
        self.addCleanup(stream.close)
        with socket.create_connection(stream.server.getsockname(), timeout=1) as client:
            self.assertEqual(client.recv(2), b"")
        factory.assert_not_called()

    def test_provider_receives_the_exact_bytes_used_by_the_meter(self):
        captured = {}
        ready = threading.Event()

        def factory(device, consume, failed):
            captured.update(consume=consume, failed=failed)
            return Mock(start=ready.set)

        stream = MicrophoneStream(factory)
        self.addCleanup(stream.close)
        stream.arm({"uid": "synthetic", "name": "Synthetic"})
        with socket.create_connection(stream.server.getsockname(), timeout=1) as client:
            self.assertTrue(ready.wait(1))
            self.assertTrue(stream.wait_ready(1))
            data = struct.pack("<320h", *([12000] * 320))
            captured["consume"](data)
            self.assertEqual(client.recv(len(data)), data)
            self.assertEqual(stream.snapshot()["level"], pcm_levels(data)["level"])
            stream.pause()
            self.assertFalse(stream.snapshot()["recording"])
            captured["consume"](data)
            client.settimeout(0.1)
            with self.assertRaises(socket.timeout):
                client.recv(2)

    def test_old_capture_cannot_feed_a_new_session(self):
        callbacks = []

        def factory(device, consume, failed):
            callbacks.append(consume)
            return Mock()

        stream = MicrophoneStream(factory)
        self.addCleanup(stream.close)
        stream.arm({"uid": "first", "name": "First"})
        with socket.create_connection(stream.server.getsockname(), timeout=1):
            self.assertTrue(stream.wait_ready(1))
            old = callbacks[0]
            stream.arm({"uid": "second", "name": "Second"})
            with socket.create_connection(stream.server.getsockname(), timeout=1):
                self.assertTrue(stream.wait_ready(1))
                old(struct.pack("<h", 30000))
                self.assertEqual(stream.snapshot()["level"], 0)

    def test_stop_drains_the_final_capture_buffer_before_provider_finalization(self):
        tail = struct.pack("<320h", *([3210] * 320))

        def factory(device, consume, failed):
            # Native capture may deliver its last queued callback while stopping.
            return Mock(stop=lambda: consume(tail))

        stream = MicrophoneStream(factory)
        self.addCleanup(stream.close)
        stream.arm({"uid": "synthetic", "name": "Synthetic"})
        with socket.create_connection(stream.server.getsockname(), timeout=1) as client:
            self.assertTrue(stream.wait_ready(1))
            stream.pause()
            self.assertEqual(client.recv(len(tail)), tail)
            self.assertTrue(stream.drained.is_set())
            self.assertFalse(stream.snapshot()["recording"])

    def test_capture_failure_is_reported_without_exception_text(self):
        stream = MicrophoneStream(Mock(side_effect=RuntimeError("private data")))
        self.addCleanup(stream.close)
        stream.arm({"uid": "synthetic", "name": "Synthetic"})
        with socket.create_connection(stream.server.getsockname(), timeout=1):
            self.assertFalse(stream.wait_ready(1))
        value = stream.snapshot()
        self.assertEqual(value["error"], "audio_stream_failed")
        self.assertNotIn("private data", json.dumps(value))

    def test_a_short_syllable_is_not_lost_between_meter_polls(self):
        callbacks = []

        def factory(device, consume, failed):
            callbacks.append(consume)
            return Mock()

        stream = MicrophoneStream(factory)
        self.addCleanup(stream.close)
        stream.arm({"uid": "synthetic", "name": "Synthetic"})
        with socket.create_connection(stream.server.getsockname(), timeout=1):
            self.assertTrue(stream.wait_ready(1))
            with patch("agy_dictation.audio_stream.time.monotonic", return_value=10) as now:
                callbacks[0](struct.pack("<160h", *([16000] * 160)))
                now.return_value = 10.01
                callbacks[0](bytes(320))
                now.return_value = 10.03
                self.assertGreater(stream.snapshot()["level"], 0.8)
                now.return_value = 10.1
                self.assertEqual(stream.snapshot()["level"], 0)

    def test_stale_meter_is_not_displayed_as_live(self):
        status = AudioStatus(lambda: "{}")
        status.value = {"level": 1, "peak": 1, "recording": True, "signal_present": True}
        status.updated = time.monotonic() - 2
        value = status.snapshot()
        self.assertEqual(value["level"], 0)
        self.assertFalse(value["signal_present"])
        self.assertIsNone(value["warning"])
        self.assertEqual(level_text(value), "목소리에 따라 막대가 움직여요")


class InputFeedbackTests(unittest.TestCase):
    def sample(self, level=0.6, peak=0.5):
        return {"recording": True, "signal_present": True, "level": level, "peak": peak}

    def test_normal_syllables_and_pauses_never_change_the_text(self):
        feedback = InputFeedback()
        messages = set()
        for index in range(400):
            # Repeated short words and pauses, plus a two-second breath every ten seconds.
            level = 0 if index % 100 > 80 or index % 10 > 4 else 0.55
            result = feedback.update(self.sample(level), index / 10)
            self.assertIsNone(result["warning"])
            messages.add(level_text(result))
        self.assertEqual(messages, {"목소리에 따라 막대가 움직여요"})

    def test_low_warning_requires_sustained_low_input_and_stable_recovery(self):
        feedback = InputFeedback()
        for index in range(80):
            result = feedback.update(self.sample(0.02), index / 10)
            self.assertIsNone(result["warning"])
        self.assertEqual(feedback.update(self.sample(0.02), 8.1)["warning"], "low")
        for index in range(82, 105):
            result = feedback.update(self.sample(0.65), index / 10)
        self.assertIsNone(result["warning"])

    def test_single_loud_peak_does_not_flash_a_warning(self):
        feedback = InputFeedback()
        feedback.update(self.sample(), 0)
        feedback.update(self.sample(0.98, 1), 0.1)
        for index in range(2, 30):
            self.assertIsNone(feedback.update(self.sample(), index / 10)["warning"])

    def test_meter_reacts_in_two_frames_and_releases_without_changing_audio(self):
        feedback = InputFeedback()
        original = self.sample(1)
        feedback.update(self.sample(0), 0)
        first = feedback.update(original, 1 / 30)["level"]
        self.assertGreater(first, 0.7)
        rising = feedback.update(original, 2 / 30)["level"]
        self.assertGreater(rising, 0.9)
        falling = feedback.update(self.sample(0), 2 / 30 + 0.25)["level"]
        self.assertLess(falling, 0.1)
        self.assertEqual(original["level"], 1)

    def test_first_available_sample_is_visible_immediately(self):
        self.assertAlmostEqual(InputFeedback().update(self.sample(0.7), 0)["level"], 0.55)

    def test_background_noise_does_not_prefill_the_meter(self):
        # Synthetic PCM at about -42 dB previously filled almost 30% of the bar.
        measurement = pcm_levels(struct.pack("<320h", *([260, -260] * 160)))
        self.assertAlmostEqual(measurement["level"], 0.3, delta=0.01)
        sample = {**self.sample(), **measurement}
        feedback = InputFeedback()
        for index in range(30):
            self.assertEqual(feedback.update(sample, index / 30)["level"], 0)
        self.assertEqual(sample["db"], measurement["db"])
        self.assertEqual(sample["level"], measurement["level"])

    def test_display_scale_tracks_input_strength_above_noise_floor(self):
        for db, expected in ((-80, 0), (-40, 0), (-39, 0.025), (-30, 0.25),
                             (-20, 0.5), (-10, 0.75), (0, 1)):
            with self.subTest(db=db):
                sample = {**self.sample((db + 60) / 60), "db": db}
                self.assertAlmostEqual(InputFeedback().update(sample, 0)["level"], expected)

    def test_speech_returns_to_fully_empty_meter_on_silence_or_background_noise(self):
        for quiet_level in (0, 0.3):
            with self.subTest(quiet_level=quiet_level):
                feedback = InputFeedback()
                feedback.update(self.sample(1), 0)
                for index in range(1, 16):
                    value = feedback.update(self.sample(quiet_level), index / 30)
                self.assertEqual(value["level"], 0)

    def test_inactive_failed_or_missing_input_clears_a_previously_full_meter(self):
        for state in ({"recording": False}, {"signal_present": False},
                      {"error": "audio_stream_failed"}, {"selected_missing": True}):
            with self.subTest(state=state):
                feedback = InputFeedback()
                feedback.update(self.sample(1), 0)
                value = feedback.update({**self.sample(1), **state}, 1 / 30)
                self.assertEqual(value["level"], 0)

    def test_a_new_recording_does_not_inherit_previous_warnings(self):
        feedback = InputFeedback()
        feedback.update(self.sample(0), 0)
        self.assertEqual(feedback.update(self.sample(0), 9)["warning"], "low")
        feedback.update({"recording": False}, 9.1)
        self.assertIsNone(feedback.update(self.sample(), 10)["warning"])

    def test_missing_samples_are_not_mislabeled_as_quiet_speech(self):
        feedback = InputFeedback()
        sample = {**self.sample(0), "signal_present": False}
        feedback.update(sample, 0)
        self.assertIsNone(feedback.update(sample, 30)["warning"])


class VoiceSessionTests(unittest.TestCase):
    def session(self):
        mic = Mock()
        mic.snapshot.return_value = {"error": None}
        mic.wait_ready.return_value = True
        settings = Mock()
        settings.load.return_value = Preferences(microphone_uid="selected")
        return VoiceSession(Mock(), mic, settings, Mock(return_value=[]), Mock())

    def test_selected_device_is_armed_before_provider_starts(self):
        voice = self.session()
        events = []
        voice.microphone.arm.side_effect = lambda _: events.append("armed")
        voice.cli.begin.side_effect = lambda: events.append("provider")
        voice.begin()
        voice.choose.assert_called_once_with("selected")
        self.assertEqual(events, ["armed", "provider"])

    def test_fast_meter_polling_does_not_repeat_device_and_settings_io(self):
        voice = self.session()
        voice.choose.return_value = {"name": "Synthetic"}
        with patch("agy_dictation.voice_session.time.monotonic", return_value=10) as now:
            for _ in range(30):
                voice.audio_status()
            voice.devices.assert_called_once()
            voice.settings.load.assert_called_once()
            self.assertEqual(voice.microphone.snapshot.call_count, 30)
            voice.begin()
            voice.audio_status()
            self.assertEqual(voice.devices.call_count, 2)
            now.return_value = 11.1
            voice.audio_status()
            self.assertEqual(voice.devices.call_count, 3)

    def test_missing_device_does_not_start_capture_or_provider(self):
        voice = self.session()
        voice.choose.side_effect = ipc.RemoteError("microphone_missing")
        with self.assertRaises(ipc.RemoteError):
            voice.begin()
        voice.microphone.arm.assert_not_called()
        voice.cli.begin.assert_not_called()

    def test_stop_capture_precedes_finalization_and_stream_closes_afterwards(self):
        voice = self.session()
        calls = []
        voice.microphone.pause.side_effect = lambda: calls.append("pause")
        voice.cli.finish.side_effect = lambda: calls.append("finish") or "synthetic"
        voice.microphone.disarm.side_effect = lambda: calls.append("close")
        self.assertEqual(voice.finish(), "synthetic")
        self.assertEqual(calls, ["pause", "finish", "close"])

    def test_failed_audio_never_claims_a_valid_transcript(self):
        voice = self.session()
        voice.microphone.snapshot.return_value = {"error": "audio_stream_failed"}
        with self.assertRaises(ipc.RemoteError):
            voice.finish()
        voice.cli.finish.assert_not_called()
        voice.cli.cancel.assert_called_once()
        voice.microphone.disarm.assert_called_once()

    def test_provider_failure_and_cancellation_both_stop_capture(self):
        voice = self.session()
        voice.cli.begin.side_effect = ipc.RemoteError("voice_unavailable")
        with self.assertRaises(ipc.RemoteError):
            voice.begin()
        voice.microphone.disarm.assert_called_once()
        voice.cancel()
        self.assertEqual(voice.microphone.disarm.call_count, 2)
