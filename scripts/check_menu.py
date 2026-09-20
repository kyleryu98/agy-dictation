#!/usr/bin/env python3
"""Opt-in native menu/settings check with synthetic devices and audio only."""

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "work/menu-check")
    parser.add_argument("--preview-seconds", type=float, default=0)
    parser.add_argument("--preview-edit", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "work"):
        parser.error("Output must be inside the ignored repository work directory.")
    output.mkdir(parents=True, exist_ok=True)
    import AppKit as A
    import CoreMedia as CM
    import CoreAudio as CA
    import CoreFoundation as CF
    from agy_dictation.settings import Settings
    from agy_dictation.macos.menu import MenuBar
    from agy_dictation.macos.hud import DictationHUD, pump_events
    from agy_dictation.macos.audio import sample_bytes

    class SyntheticLogin:
        value = False

        def enabled(self):
            return self.value

        def set_enabled(self, value):
            self.value = value

    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    before = A.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
    app.finishLaunching()
    settings = Settings(output / "preferences")
    settings.change(shortcut="ctrl_grave", sounds=True, microphone_uid=None)
    actions, changes = [], []
    login = SyntheticLogin()
    capture_states = []
    menu = MenuBar(settings, login, actions.append, changes.append, capture_states.append)
    hud = DictationHUD(lambda: None)
    snapshot = {
        "devices": [
            {"uid": "test-usb", "name": "USB 마이크"},
            {"uid": "test-built-in", "name": "MacBook 마이크"},
        ],
        "selected_name": "USB 마이크",
        "recording": False,
        "level": 0,
    }
    try:
        menu.update("idle", snapshot)
        pump_events(app, 0.05)
        assert A.NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() == before
        # Opening Settings is an explicit user action and should take keyboard focus.
        menu.show_settings()
        pump_events(app, 0.02)
        assert menu.item.isVisible()
        assert menu.window.isVisible() and menu.window.isKeyWindow()
        assert menu.window.title() == "AGY Dictation 설정"
        assert menu.status.title().startswith("AGY Dictation ·")
        assert menu.quit.title() == "AGY Dictation 종료"
        (output / "preview-window.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "window_id": menu.window.windowNumber(),
                }
            )
        )
        if args.preview_edit:
            menu.shortcut_editor.begin()
            menu.shortcut_editor.propose(40, ("control", "option"), "K")
        print("Synthetic settings preview ready.", flush=True)
        until = time.monotonic() + min(30, max(0, args.preview_seconds))
        while time.monotonic() < until:
            pump_events(app, 0.05)
        menu.shortcut_editor.cancel()
        menu.input_popup.selectItemAtIndex_(1)
        menu.actions.microphonePopup_(menu.input_popup)
        assert settings.value.microphone_uid == "test-usb"
        editor = menu.shortcut_editor
        editor.begin()
        assert editor.monitor is not None
        assert capture_states[-1] is True
        event = A.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            A.NSEventTypeKeyDown,
            (0, 0),
            A.NSEventModifierFlagControl | A.NSEventModifierFlagOption,
            0,
            menu.window.windowNumber(),
            None,
            "d",
            "d",
            False,
            2,
        )
        assert menu.window.performKeyEquivalent_(event)
        assert editor.draft.label == "⌃ ⌥ D"
        assert settings.value.shortcut == "ctrl_grave"  # Preview is not a save.
        enter = A.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            A.NSEventTypeKeyDown,
            (0, 0),
            0,
            0,
            menu.window.windowNumber(),
            None,
            "\r",
            "\r",
            False,
            36,
        )
        assert menu.window.performKeyEquivalent_(enter)
        assert settings.value.shortcut == "custom"
        assert settings.value.binding.key_code == 2
        assert capture_states[-1] is False
        previous = settings.value.binding
        editor.begin()
        editor.propose(0, (), "A")
        assert editor.draft is None and not editor.save_button.isEnabled()
        editor.cancel()
        assert editor.monitor is None
        assert settings.value.binding == previous
        editor.begin()
        command_q = A.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            A.NSEventTypeKeyDown, (0, 0), A.NSEventModifierFlagCommand, 0,
            menu.window.windowNumber(), None, "q", "q", False, 12,
        )
        assert editor.handle_event(command_q) is None
        assert editor.draft.key_code == 12 and menu.window.isVisible()
        editor.cancel()
        editor.begin()
        for flags in (A.NSEventModifierFlagCommand, 0):
            modifier = A.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                A.NSEventTypeFlagsChanged,
                (0, 0),
                flags,
                0,
                menu.window.windowNumber(),
                None,
                "",
                "",
                False,
                55,
            )
            editor.capture.flagsChanged_(modifier)
        assert editor.draft.modifier_only and editor.draft.key_code == 55
        editor.save()
        assert settings.value.binding.label == "왼쪽 ⌘"
        editor.begin()
        menu.actions.windowDidResignKey_(None)
        assert not editor.editing and capture_states[-1] is False
        editor.actions.clear_(None)
        assert settings.value.binding is None
        editor.actions.reset_(None)
        assert settings.value.shortcut == "ctrl_grave"
        assert not actions  # No recording command while editing a shortcut.
        menu.sounds.setState_(0)
        menu.actions.sounds_(menu.sounds)
        assert not settings.value.sounds
        menu.autostart.setState_(1)
        menu.actions.login_(menu.autostart)
        assert login.value
        assert Settings(settings.base).load() == settings.value
        menu.update("idle", snapshot)
        assert menu.input_menu.itemAtIndex_(1).state() == 1

        # A real CoreMedia sample made from synthetic PCM exercises native decoding.
        pcm = struct.pack("<320h", *([4000] * 320))
        asbd = CA.AudioStreamBasicDescription(
            16000, int.from_bytes(b"lpcm", "big"), 12, 2, 1, 2, 1, 16, 0
        )
        error, description = CM.CMAudioFormatDescriptionCreate(
            None, asbd, 0, None, 0, None, None, None
        )
        assert error == 0
        error, block = CM.CMBlockBufferCreateWithMemoryBlock(
            None, pcm, len(pcm), CF.kCFAllocatorNull, None, 0, len(pcm), 0, None
        )
        assert error == 0
        timing = CM.CMSampleTimingInfo(CM.CMTimeMake(1, 16000), CM.kCMTimeZero, CM.kCMTimeInvalid)
        error, sample = CM.CMSampleBufferCreateReady(
            None, block, description, 320, 1, [timing], 1, [2], None
        )
        assert error == 0 and sample_bytes(sample) == pcm

        recording = {
            **snapshot,
            "recording": True,
            "signal_present": True,
            "input_name": "USB 마이크",
            "level": 0.65,
            "peak": 0.35,
            "db": -21,
        }
        menu.update("recording", recording)
        menu.change(microphone_uid="test-built-in")
        assert settings.value.microphone_uid == "test-usb"
        assert not menu.input_popup.isEnabled() and not menu.configure.isEnabled()
        hud.shortcut_label = settings.value.shortcut_label
        hud.update("recording")
        hud.set_audio_status(recording)
        assert abs(hud.meter.doubleValue() - 0.65) < 0.001
        assert abs(menu.level.doubleValue() - 0.65) < 0.001
        hud.set_audio_status({**recording, "level": 0})
        menu.update("recording", {**recording, "level": 0})
        assert hud.meter.doubleValue() == menu.level.doubleValue() == 0
        assert "USB 마이크" in hud.subtitle.stringValue()
        menu.actions.toggle_(None)
        menu.tracking = True
        menu.perform_pending()
        assert not actions
        menu.tracking = False
        menu.perform_pending()
        assert actions == ["toggle"]
        hud.update("idle", "입력 완료")
        end = time.monotonic() + 1
        while time.monotonic() < end:
            hud.tick()
            pump_events(app, 0.02)
        assert not hud.visible and not hud.panel.isVisible()
        # Shortcut editing deliberately gives the settings window keyboard focus.
        # The passive HUD itself must still never become a key window.
        assert not hud.panel.isKeyWindow()
        result = {
            "menu_settings_controls": True,
            "custom_shortcut_preview_save_cancel_disable_reset": True,
            "preferences_reloaded": True,
            "recording_locks_settings": True,
            "menu_actions_deferred": True,
            "native_pcm_bytes_exact": True,
            "hud_uses_measured_level": True,
            "passive_menu_focus_unchanged": True,
            "settings_keyboard_focus": True,
            "hud_never_key": True,
            "real_microphone_used": False,
            "login_state_changed": False,
        }
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print("Native menu, settings, synthetic PCM and HUD checks passed.")
    finally:
        hud.hide()
        menu.close()
        pump_events(app, 0)


if __name__ == "__main__":
    main()
