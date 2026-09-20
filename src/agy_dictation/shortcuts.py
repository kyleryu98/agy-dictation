"""Custom shortcut validation and modifier-tap policy, independent of native UI."""

from dataclasses import dataclass

MODIFIER_ORDER = ("control", "option", "shift", "command")
MODIFIER_SYMBOLS = {"control": "⌃", "option": "⌥", "shift": "⇧", "command": "⌘"}
MODIFIER_KEYS = {
    55: ("command", "왼쪽 ⌘"),
    54: ("command", "오른쪽 ⌘"),
    59: ("control", "왼쪽 ⌃"),
    62: ("control", "오른쪽 ⌃"),
    58: ("option", "왼쪽 ⌥"),
    61: ("option", "오른쪽 ⌥"),
    56: ("shift", "왼쪽 ⇧"),
    60: ("shift", "오른쪽 ⇧"),
}
FUNCTION_KEYS = {
    code: f"F{i + 1}"
    for i, code in enumerate(
        (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113, 106, 64, 79, 80, 90)
    )
}
SPECIAL_KEYS = {
    36: "Return",
    48: "Tab",
    49: "Space",
    50: "₩",
    51: "Delete",
    53: "Esc",
    76: "Enter",
    117: "⌦",
    123: "←",
    124: "→",
    125: "↓",
    126: "↑",
    115: "Home",
    119: "End",
    116: "Page Up",
    121: "Page Down",
    **FUNCTION_KEYS,
}


@dataclass(frozen=True)
class Shortcut:
    key_code: int
    modifiers: tuple[str, ...]
    key_label: str

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) != {"key_code", "modifiers", "key_label"}:
            raise ValueError("단축키를 다시 지정해 주세요.")
        code, modifiers, label = data["key_code"], data["modifiers"], data["key_label"]
        if type(code) is not int or not 0 <= code <= 127:
            raise ValueError("이 키는 사용할 수 없어요.")
        if (
            not isinstance(modifiers, (list, tuple))
            or any(not isinstance(m, str) or m not in MODIFIER_ORDER for m in modifiers)
            or len(set(modifiers)) != len(modifiers)
        ):
            raise ValueError("보조 키 조합을 다시 지정해 주세요.")
        modifiers = tuple(m for m in MODIFIER_ORDER if m in modifiers)
        if not isinstance(label, str) or not 1 <= len(label) <= 20 or not label.isprintable():
            raise ValueError("키 이름을 확인하지 못했어요.")
        if code in MODIFIER_KEYS:
            if modifiers:
                raise ValueError("보조 키 하나를 누르거나 다른 키와 조합해 주세요.")
            label = MODIFIER_KEYS[code][1]
        elif code not in FUNCTION_KEYS and not set(modifiers) & {"control", "option", "command"}:
            raise ValueError("⌘, ⌃ 또는 ⌥와 함께 눌러 주세요.")
        if code in (48, 49) and modifiers == ("command",):
            raise ValueError("앱 전환·검색에 쓰는 키예요. 다른 조합을 눌러 주세요.")
        return cls(code, modifiers, label)

    @property
    def label(self):
        return " ".join([*(MODIFIER_SYMBOLS[m] for m in self.modifiers), self.key_label])

    @property
    def modifier_only(self):
        return self.key_code in MODIFIER_KEYS and not self.modifiers

    def to_dict(self):
        return {
            "key_code": self.key_code,
            "modifiers": list(self.modifiers),
            "key_label": self.key_label,
        }


class ModifierTap:
    """A modifier-only binding fires on a short solo release, never inside ⌘C etc."""

    def __init__(self):
        self.started = None
        self.binding = None

    def reset(self):
        self.started = self.binding = None

    def feed(self, binding, kind, code, modifiers, now):
        if binding != self.binding:
            self.started = None
            self.binding = binding
        if binding is None or not binding.modifier_only:
            return False
        own_modifier = MODIFIER_KEYS[binding.key_code][0]
        if kind == "down" or kind == "flags" and code != binding.key_code:
            self.started = None
            return False
        if kind != "flags":
            return False
        if own_modifier in modifiers:
            if modifiers == {own_modifier}:
                self.started = now
            else:
                self.started = None
            return False
        started, self.started = self.started, None
        return started is not None and not modifiers and 0 <= now - started <= 0.7
