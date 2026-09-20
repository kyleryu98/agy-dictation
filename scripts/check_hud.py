#!/usr/bin/env python3
"""Opt-in native HUD check: no service, microphone, hotkeys, typing or permissions.

Shows only this process's non-activating test panel. Checks WindowServer metadata
as well as AppKit state; offscreen rendering reads only the synthetic HUD itself.
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "work/hud-check")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This check requires a macOS desktop session.")
    # Deliberately do not import the service or provider/input frameworks.
    import AppKit as A
    import Quartz as Q
    import objc
    from agy_dictation.macos.hud import DictationHUD, pump_events, rect_values

    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "work"):
        parser.error("Output must be inside the repository's ignored work directory.")
    output.mkdir(parents=True, exist_ok=True)
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    workspace = A.NSWorkspace.sharedWorkspace()
    foreground = workspace.frontmostApplication().processIdentifier()
    app.finishLaunching()
    cancelled = []
    hud = DictationHUD(lambda: cancelled.append(True))
    records = []

    def advance(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            with objc.autorelease_pool():
                hud.tick()
                pump_events(app, timeout=min(0.02, max(0, end - time.monotonic())))

    def check(stage, visible, alpha=None):
        number = hud.panel.windowNumber()
        windows = Q.CGWindowListCopyWindowInfo(
            Q.kCGWindowListOptionOnScreenOnly, Q.kCGNullWindowID
        )
        window = next((w for w in windows if w[Q.kCGWindowNumber] == number), None)
        record = {
            "stage": stage,
            "hud_visible": hud.visible,
            "appkit_visible": bool(hud.panel.isVisible()),
            "server_visible": window is not None,
            "alpha": round(hud.panel.alphaValue(), 3),
            "server_alpha": round(window[Q.kCGWindowAlpha], 3) if window else None,
            "key_window": bool(hud.panel.isKeyWindow()),
            "foreground_unchanged": (
                workspace.frontmostApplication().processIdentifier() == foreground
            ),
            "frame": rect_values(hud.panel.frame()),
        }
        records.append(record)
        assert record["hud_visible"] == record["appkit_visible"] == visible, record
        assert record["server_visible"] == visible, record
        assert not record["key_window"] and record["foreground_unchanged"], record
        if visible:
            assert any(
                A.NSContainsRect(screen.visibleFrame(), hud.panel.frame())
                for screen in A.NSScreen.screens()
            ), record
        if alpha is not None:
            assert abs(record["alpha"] - alpha) < 0.02, record
            assert abs(record["server_alpha"] - alpha) < 0.02, record

    def render():
        view = hud.panel.contentView()
        bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
        width, height = bitmap.pixelsWide(), bitmap.pixelsHigh()
        corners = [bitmap.colorAtX_y_(x, y).alphaComponent() for x, y in (
            (0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)
        )]
        fill = bitmap.colorAtX_y_(width // 2, height // 2).colorUsingColorSpace_(
            A.NSColorSpace.sRGBColorSpace()
        )
        assert max(corners) == 0, corners
        assert fill.alphaComponent() > 0.95, fill.alphaComponent()
        assert max(fill.redComponent(), fill.greenComponent(), fill.blueComponent()) < 0.3
        png = bitmap.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {})
        assert png.writeToFile_atomically_(str(output / "recording.png"), True)

    try:
        hud.update("recording")
        advance(0.15)
        check("recording", True, 1)
        render()
        hud.update("transcribing")
        advance(0.15)
        check("processing", True, 1)
        hud.update("inserting")
        hud.update("idle", "입력 완료")
        advance(0.74)
        check("completion-fading", True)
        assert 0 < hud.panel.alphaValue() < 1
        hud.update("recording")
        advance(0.15)
        check("recording-interrupts-fade", True, 1)
        advance(0.2)
        check("old-deadline-cannot-hide-recording", True, 1)
        hud.close.performClick_(None)
        assert cancelled == [True]
        hud.update("idle", "녹음을 취소했습니다.")
        advance(1.05)
        check("cancel-dismissed", False)
        for index in range(3):
            hud.update("recording")
            advance(0.1)
            check(f"repeat-{index}-recording", True, 1)
            hud.update("idle", "입력 완료")
            advance(1.05)
            check(f"repeat-{index}-completed", False)
        hud.update("transcribing")
        advance(0.1)
        hud.update("idle", "준비됨")
        advance(0.1)
        check("ready-clears-processing", False)
        hud.update("error", "검증용 오류")
        advance(0.1)
        check("error", True, 1)
        advance(6.1)
        check("error-dismissed", False)
    finally:
        hud.hide()
        pump_events(app, timeout=0)
        (output / "result.json").write_text(json.dumps(records, indent=2) + "\n")
    print(f"Native HUD check passed: {len(records)} WindowServer/focus checks; dark rounded render.")
    print("Physical display hotplug and installed-service acceptance remain separate checks.")


if __name__ == "__main__":
    main()
