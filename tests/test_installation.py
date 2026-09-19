import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("manager", ROOT / "scripts/manage_macos.py")
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.layout = m.Layout(
            self.root / "data",
            self.root / "logs",
            self.root / "apps/ProListen Voice Engine.app",
            self.root / "agents/com.prolisten.agy-dictation.plist",
            self.root / "checkout",
            sys.executable,
        )
        self.build = self.root / "build"
        app = self.build / self.layout.app.name
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Resources").mkdir()
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": m.ENGINE_BUNDLE_ID})
        )
        (app / "Contents/MacOS/AGYVoiceEngine").write_text("test-only launcher")
        (app / "Contents/Resources/engine_bootstrap.py").write_text("# test only")
        (self.build / f"{self.layout.label}.plist").write_bytes(
            plistlib.dumps(
                {
                    "Label": self.layout.label,
                    "ProgramArguments": [sys.executable, "-m", "agy_dictation.macos.service"],
                    "WorkingDirectory": str(self.layout.base),
                    "EnvironmentVariables": {"PYTHONPATH": str(self.layout.source / "src")},
                }
            )
        )
        editor = self.build / "support/bin" / self.layout.editor.name
        editor.parent.mkdir(parents=True)
        editor.write_text("#!/bin/sh\nexit 0\n")
        self.run = Mock(return_value=subprocess.CompletedProcess([], 0, ""))

    def tearDown(self):
        self.temp.cleanup()

    def test_install_stages_files_only_without_starting_service(self):
        m.install(self.build, self.layout, run=self.run)
        self.assertTrue(self.layout.app.is_dir())
        self.assertTrue(self.layout.agent.exists())
        self.assertEqual(self.layout.agent.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.layout.editor.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.layout.base / "voice-session").is_dir())
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.run.call_args.args[0][:2], ["/usr/bin/codesign", "--verify"])

    def test_existing_install_is_never_overwritten(self):
        self.layout.app.mkdir(parents=True)
        marker = self.layout.app / "keep"
        marker.write_text("existing")
        with self.assertRaises(ValueError):
            m.install(self.build, self.layout, run=self.run)
        self.assertEqual(marker.read_text(), "existing")
        self.run.assert_not_called()

    def test_wrong_interpreter_build_is_rejected(self):
        bad = self.build / f"{self.layout.label}.plist"
        data = plistlib.loads(bad.read_bytes())
        data["ProgramArguments"][0] = "/wrong/python"
        bad.write_bytes(plistlib.dumps(data))
        with self.assertRaises(ValueError):
            m.install(self.build, self.layout, run=self.run)
        self.assertFalse(self.layout.app.exists())
        self.run.assert_not_called()

    def test_uninstall_preserves_recovery_logs_and_checkout(self):
        m.install(self.build, self.layout, run=self.run)
        recovery = self.layout.base / "last-transcript.txt"
        recovery.write_text("synthetic recovery")
        stop = Mock()
        m.uninstall(self.layout, stop_service=stop)
        stop.assert_called_once_with(self.layout)
        self.assertFalse(self.layout.app.exists())
        self.assertFalse(self.layout.agent.exists())
        self.assertEqual(recovery.read_text(), "synthetic recovery")
        self.assertTrue(self.layout.logs.exists())

    def test_tampered_installation_record_prevents_removal(self):
        m.install(self.build, self.layout, run=self.run)
        record = self.layout.base / "installation.json"
        data = json.loads(record.read_text())
        data["app"] = "/different/app"
        record.write_text(json.dumps(data))
        stop = Mock()
        with self.assertRaises(ValueError):
            m.uninstall(self.layout, stop_service=stop)
        stop.assert_not_called()
        self.assertTrue(self.layout.app.exists())

    def test_default_command_is_read_only(self):
        env = os.environ.copy()
        env["AGY_DICTATION_DATA_DIR"] = str(self.root / "untouched")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/manage_macos.py"), "install"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Plan only", result.stdout)
        self.assertFalse((self.root / "untouched").exists())

    def test_start_blocks_legacy_service(self):
        m.install(self.build, self.layout, run=self.run)
        with patch.object(m, "loaded", return_value=True):
            with self.assertRaisesRegex(ValueError, "Legacy"):
                m.start(self.layout, run=self.run)
        self.assertEqual(self.run.call_count, 1)

    def test_install_failure_rolls_back_only_new_files(self):
        import shutil

        real_copy = shutil.copy2

        def fail(source, destination, *args, **kwargs):
            if Path(destination) == self.layout.editor:
                raise OSError("copy failed")
            return real_copy(source, destination, *args, **kwargs)

        with patch.object(m.shutil, "copy2", side_effect=fail):
            with self.assertRaises(OSError):
                m.install(self.build, self.layout, run=self.run)
        self.assertFalse(self.layout.app.exists())
        self.assertFalse(self.layout.agent.exists())

    def test_stale_socket_cleanup_requires_unheld_engine_lock(self):
        import fcntl
        import socket

        m.sf.ensure_private_dir(self.layout.base)
        with m.sf.private_directory(self.layout.base) as directory:
            fd = m.sf.open_private_file(directory, "engine.lock", os.O_RDWR | os.O_CREAT)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self.layout.base / "engine.sock"))
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(m.remove_stale_socket(self.layout))
            fcntl.flock(fd, fcntl.LOCK_UN)
            self.assertTrue(m.remove_stale_socket(self.layout))
            self.assertFalse((self.layout.base / "engine.sock").exists())
        finally:
            sock.close()
            os.close(fd)

    def test_concurrent_installation_is_rejected(self):
        with m.installation_lock(self.layout):
            with self.assertRaisesRegex(ValueError, "Another installation"):
                m.install(self.build, self.layout, run=self.run)
        self.assertFalse(self.layout.app.exists())

    def test_status_without_install_is_read_only(self):
        self.assertEqual(m.runtime_state(self.layout), "not_started")
        self.assertFalse(self.layout.base.exists())

    def test_status_does_not_follow_symlink_or_print_arbitrary_details(self):
        m.sf.ensure_private_dir(self.layout.base)
        secret = self.root / "other.json"
        secret.write_text(json.dumps({"state": "private data"}))
        (self.layout.base / "status.json").symlink_to(secret)
        self.assertEqual(m.runtime_state(self.layout), "unavailable")

    def test_start_registers_only_this_service(self):
        m.install(self.build, self.layout, run=self.run)
        self.run.reset_mock()
        with patch.object(m, "loaded", return_value=False):
            m.start(self.layout, run=self.run)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands[0], ["/bin/launchctl", "enable", f"gui/{os.getuid()}/{self.layout.label}"])
        self.assertEqual(commands[1], ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(self.layout.agent)])

    def test_stop_does_not_remove_files(self):
        m.install(self.build, self.layout, run=self.run)
        engine_shutdown = Mock()
        with patch.object(m, "loaded", return_value=False):
            m.stop(self.layout, run=self.run, engine_shutdown=engine_shutdown)
        engine_shutdown.assert_called_once()
        self.assertTrue(self.layout.app.exists())
        self.assertTrue(self.layout.agent.exists())
