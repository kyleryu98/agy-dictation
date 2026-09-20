import tempfile
import unittest
from pathlib import Path

from agy_dictation.settings import Preferences, Settings
from agy_dictation.shortcuts import ModifierTap, Shortcut


class ShortcutTests(unittest.TestCase):
    def test_existing_preferences_keep_their_default_and_legacy_bindings(self):
        self.assertEqual(Preferences.parse({}).shortcut_codes, (50, 42))
        old = Preferences.parse({"shortcut": "ctrl_option_d", "sounds": False})
        self.assertEqual(old.binding.label, "⌃ ⌥ D")

    def test_custom_combination_round_trips_without_losing_modifiers(self):
        binding = Shortcut.parse(
            {
                "key_code": 40,
                "modifiers": ["command", "control", "shift"],
                "key_label": "K",
            }
        )
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(Path(folder) / "settings")
            settings.change(shortcut="custom", custom_shortcut=binding.to_dict())
            self.assertEqual(Settings(settings.base).load().binding, binding)
            settings.change(shortcut="disabled", custom_shortcut=None)
            self.assertIsNone(Settings(settings.base).load().binding)
            settings.change(shortcut="ctrl_grave")
            self.assertEqual(Settings(settings.base).load().shortcut_codes, (50, 42))

    def test_typing_keys_alone_are_not_global_shortcuts(self):
        for code, label in ((0, "A"), (49, "Space"), (53, "Esc")):
            with self.subTest(code=code), self.assertRaises(ValueError):
                Shortcut.parse({"key_code": code, "modifiers": [], "key_label": label})
        function = Shortcut.parse({"key_code": 96, "modifiers": [], "key_label": "F5"})
        self.assertEqual(function.label, "F5")

    def test_invalid_or_reserved_combinations_are_rejected(self):
        for data in (
            {"key_code": True, "modifiers": ["control"], "key_label": "X"},
            {"key_code": 2, "modifiers": ["unknown"], "key_label": "D"},
            {"key_code": 2, "modifiers": ["command", "command"], "key_label": "D"},
            {"key_code": 49, "modifiers": ["command"], "key_label": "Space"},
            {"key_code": 2, "modifiers": ["control"], "key_label": "bad\nlabel"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Shortcut.parse(data)

    def test_modifier_tap_is_distinct_from_a_chord_long_hold_or_other_side(self):
        binding = Shortcut.parse({"key_code": 55, "modifiers": [], "key_label": "Command"})
        tap = ModifierTap()
        self.assertFalse(tap.feed(binding, "flags", 55, {"command"}, 0))
        self.assertTrue(tap.feed(binding, "flags", 55, set(), 0.15))
        tap.feed(binding, "flags", 55, {"command"}, 1)
        tap.feed(binding, "down", 8, {"command"}, 1.1)  # Command-C
        self.assertFalse(tap.feed(binding, "flags", 55, set(), 1.2))
        tap.feed(binding, "flags", 55, {"command"}, 2)
        self.assertFalse(tap.feed(binding, "flags", 55, set(), 3))
        tap.feed(binding, "flags", 54, {"command"}, 4)
        self.assertFalse(tap.feed(binding, "flags", 54, set(), 4.1))
        tap.feed(binding, "flags", 55, {"command"}, 5)
        tap.reset()  # Mouse click/scroll or entering the shortcut editor.
        self.assertFalse(tap.feed(binding, "flags", 55, set(), 5.1))

    def test_corrupt_custom_setting_is_rejected_before_installing_a_binding(self):
        with self.assertRaises(ValueError):
            Preferences.parse({"shortcut": "custom"})
