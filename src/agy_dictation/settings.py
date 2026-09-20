"""Validated preferences shared by the frontend and voice helper."""

from dataclasses import asdict, dataclass, replace
from . import secure_files as sf
from .shortcuts import Shortcut

SHORTCUTS = {
    "ctrl_grave": ("Ctrl + ₩", (50, 42), ("control",)),
    "ctrl_option_d": ("Ctrl + Option + D", (2,), ("control", "option")),
    "ctrl_shift_space": ("Ctrl + Shift + Space", (49,), ("control", "shift")),
}


@dataclass(frozen=True)
class Preferences:
    shortcut: str = "ctrl_grave"
    sounds: bool = True
    microphone_uid: str | None = None
    custom_shortcut: dict | None = None

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError("Invalid settings fields")
        value = cls(**data)
        if (
            not isinstance(value.shortcut, str)
            or value.shortcut not in {*SHORTCUTS, "custom", "disabled"}
            or type(value.sounds) is not bool
        ):
            raise ValueError("Invalid shortcut or sound setting")
        if value.custom_shortcut is not None:
            Shortcut.parse(value.custom_shortcut)
        if value.shortcut == "custom" and value.custom_shortcut is None:
            raise ValueError("Custom shortcut is missing")
        uid = value.microphone_uid
        if uid is not None and (
            not isinstance(uid, str) or not 1 <= len(uid) <= 512 or "\x00" in uid
        ):
            raise ValueError("Invalid microphone selection")
        return value

    @property
    def shortcut_label(self):
        return self.binding.label if self.binding is not None else ""

    @property
    def binding(self):
        if self.shortcut == "disabled":
            return None
        if self.shortcut == "custom":
            return Shortcut.parse(self.custom_shortcut)
        _, codes, modifiers = SHORTCUTS[self.shortcut]
        label = {"ctrl_grave": "₩", "ctrl_option_d": "D", "ctrl_shift_space": "Space"}[
            self.shortcut
        ]
        return Shortcut(codes[0], modifiers, label)

    @property
    def shortcut_codes(self):
        if self.shortcut in SHORTCUTS:
            return SHORTCUTS[self.shortcut][1]
        return (self.binding.key_code,) if self.binding is not None else ()


class Settings:
    def __init__(self, base):
        self.base = base
        self.value = Preferences()

    def load(self):
        with sf.private_directory(self.base) as directory:
            try:
                data = sf.read_json(directory, "settings.json", 4096)
            except FileNotFoundError:
                self.value = Preferences()
            else:
                self.value = Preferences.parse(data)
        return self.value

    def change(self, **changes):
        value = Preferences.parse(asdict(replace(self.value, **changes)))
        with sf.private_directory(self.base) as directory:
            sf.atomic_write_json(directory, "settings.json", asdict(value))
        self.value = value
        return value
