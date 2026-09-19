import sys
import unittest
from unittest.mock import patch

if sys.platform == "darwin":
    from agy_dictation.macos import service as b
    from agy_dictation.macos.hud import presentation


@unittest.skipUnless(sys.platform == "darwin", "macOS policy tests")
class MacOSTests(unittest.TestCase):
    def setUp(self):
        b.pressed.clear()
        b.session = None
        b.cancel_requested.clear()

    def test_changed_focus_never_injects(self):
        with (
            patch.object(b, "target_unchanged", return_value=False),
            patch.object(b.Q, "CGEventPost") as post,
        ):
            with self.assertRaises(b.BridgeError):
                b.inject("text", {})
            post.assert_not_called()

    def test_auto_repeat_only_triggers_once_per_physical_key(self):
        with patch.object(b, "trigger") as fire:
            for code in (50, 42):
                event = b.Q.CGEventCreateKeyboardEvent(None, code, True)
                b.Q.CGEventSetFlags(event, b.CTRL)
                self.assertIsNone(b.intercept(b.Q.kCGEventKeyDown, event))
                self.assertIsNone(b.intercept(b.Q.kCGEventKeyDown, event))
                self.assertIsNone(b.intercept(b.Q.kCGEventKeyUp, event))
            self.assertEqual(fire.call_count, 2)

    def test_explicitly_focused_webview_input_is_resolved(self):
        attrs = {
            ("root", "AXFocusedUIElement"): "web",
            ("web", "AXRole"): "AXWebArea",
            ("web", "AXChildren"): ["input"],
            ("input", "AXRole"): "AXTextArea",
            ("input", "AXFocused"): True,
        }
        with patch.object(
            b, "attr", side_effect=lambda el, key, default=None: attrs.get((el, key), default)
        ):
            self.assertEqual(b.focused_input("root"), "input")
            attrs[("input", "AXFocused")] = False
            self.assertIsNone(b.focused_input("root"))

    def test_processing_hud_is_indeterminate(self):
        self.assertTrue(presentation("transcribing")[2])
        self.assertFalse(presentation("recording")[2])
        self.assertIsNone(presentation("idle", "준비됨"))

    def test_target_is_rechecked_after_waiting_for_modifiers(self):
        with (
            patch.object(b, "target_unchanged", side_effect=[True, False]),
            patch.object(b, "wait_modifiers"),
            patch.object(b.Q, "CGEventPost") as post,
        ):
            with self.assertRaises(b.BridgeError):
                b.inject("safe text", {})
            post.assert_not_called()
