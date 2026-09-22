import threading
import unittest
from unittest.mock import Mock

from agy_dictation.ipc import RemoteError
from agy_dictation.startup import start_backend


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        self.stopping = Mock()
        self.stopping.is_set.return_value = False
        self.stopping.wait.return_value = False
        self.report = Mock()

    def start(self):
        return start_backend(self.backend, self.stopping, self.report)

    def test_boot_timeout_recovers_without_user_action(self):
        self.backend.start.side_effect = [RemoteError("startup_timeout"), None]
        self.assertTrue(self.start())
        self.assertEqual(self.backend.start.call_count, 2)
        self.backend.close.assert_called_once()
        self.stopping.wait.assert_called_once_with(5)
        self.assertEqual(self.report.call_args.args[0], "starting")

    def test_retries_stop_after_three_attempts(self):
        self.backend.start.side_effect = RemoteError("engine_unavailable")
        with self.assertRaises(RemoteError):
            self.start()
        self.assertEqual(self.backend.start.call_count, 3)
        self.assertEqual([c.args[0] for c in self.stopping.wait.call_args_list], [5, 15])

    def test_action_required_and_unknown_errors_are_not_retried(self):
        for error in (
            RemoteError("permission_required"), RemoteError("trust_required"),
            RemoteError("terms_required"), RemoteError("login_required"),
            RuntimeError("private provider output"),
        ):
            with self.subTest(error=type(error).__name__):
                self.backend.reset_mock()
                self.backend.start.side_effect = error
                with self.assertRaises(type(error)):
                    self.start()
                self.backend.start.assert_called_once()
                self.stopping.wait.assert_not_called()
                self.report.assert_not_called()

    def test_quitting_during_backoff_prevents_another_launch(self):
        self.backend.start.side_effect = RemoteError("startup_timeout")
        self.stopping.wait.return_value = True
        self.assertFalse(self.start())
        self.backend.start.assert_called_once()

    def test_already_stopping_does_not_launch(self):
        stop = threading.Event()
        stop.set()
        self.assertFalse(start_backend(self.backend, stop, self.report))
        self.backend.start.assert_not_called()

    def test_recovery_never_starts_recording(self):
        self.backend.start.side_effect = [RemoteError("busy"), None]
        self.assertTrue(self.start())
        self.backend.begin.assert_not_called()
        self.backend.finish.assert_not_called()
        self.backend.cancel.assert_not_called()
