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
    from agy_dictation.macos.controls import InputFeedback
    from agy_dictation.macos.hud import DictationHUD, PassivePanel, pump_events, rect_values

    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "work"):
        parser.error("Output must be inside the repository's ignored work directory.")
    output.mkdir(parents=True, exist_ok=True)
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    workspace = A.NSWorkspace.sharedWorkspace()
    foreground = workspace.frontmostApplication().processIdentifier()
    app.finishLaunching()
    # Finish launch-time ordering before creating multiple synthetic panels.
    # Otherwise AppKit can activate the test process during its first event pump.
    for _ in range(10):
        pump_events(app, timeout=0.02)
    assert workspace.frontmostApplication().processIdentifier() == foreground
    cancelled = []
    hud = DictationHUD(lambda: cancelled.append(True))
    records = []
    blocker = PassivePanel.alloc().initWithContentRect_styleMask_backing_defer_(
        A.NSMakeRect(0, 0, 336, 88),
        A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel,
        A.NSBackingStoreBuffered, False,
    )
    blocker.setReleasedWhenClosed_(False)
    blocker.setHidesOnDeactivate_(False)
    blocker.setCollectionBehavior_(hud.panel.collectionBehavior())

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
            "server_level": window[Q.kCGWindowLayer] if window else None,
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

    def render(filename="recording.png"):
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
        assert png.writeToFile_atomically_(str(output / filename), True)

    try:
        hud.update("recording")
        advance(0.15)
        check("recording", True, 1)
        render()
        # A later overlapping panel must stay behind the HUD. Merely being listed
        # as visible by AppKit/WindowServer does not prove the HUD is unobscured.
        blocker.setFrame_display_(hud.panel.frame(), True)
        for level in (A.NSNormalWindowLevel, A.NSFloatingWindowLevel, A.NSModalPanelWindowLevel):
            blocker.setLevel_(level)
            blocker.orderFrontRegardless()
            advance(0.05)
            windows = Q.CGWindowListCopyWindowInfo(
                Q.kCGWindowListOptionOnScreenOnly, Q.kCGNullWindowID
            )
            numbers = [w[Q.kCGWindowNumber] for w in windows]
            above = numbers.index(hud.panel.windowNumber()) < numbers.index(blocker.windowNumber())
            records.append({"stage": "overlap", "blocker_level": level, "hud_above": above})
            assert above, records[-1]
            check(f"above-level-{level}", True, 1)
        blocker.orderOut_(None)
        assert hud.panel.level() == A.NSStatusWindowLevel
        if hasattr(A, "NSWindowCollectionBehaviorCanJoinAllApplications"):
            assert hud.panel.collectionBehavior() & A.NSWindowCollectionBehaviorCanJoinAllApplications
        # Exercise the actual local observer wiring without switching user apps
        # or desktops. Physical app/Space/Stage Manager transitions are manual.
        for name in (
            A.NSWorkspaceDidActivateApplicationNotification,
            A.NSWorkspaceActiveSpaceDidChangeNotification,
        ):
            hud.workspace_center.postNotificationName_object_(name, None)
            assert hud.pending_show
            advance(0.05)
            assert not hud.pending_show
            check(str(name), True, 1)
        feedback = InputFeedback()
        for index, (db, expected, filename) in enumerate((
            (-42, 0, "meter-quiet.png"),
            (-20, 0.5, "meter-speech.png"),
            (-42, 0, "meter-quiet-after-speech.png"),
        )):
            status = feedback.update({
                "recording": True, "signal_present": True,
                "input_name": "Synthetic input", "db": db,
                "level": max(0, (db + 60) / 60), "peak": 0.1,
            }, index * 0.5)
            hud.set_audio_status(status)
            advance(0.05)
            assert hud.progress.isHidden() and not hud.meter.isHidden()
            assert abs(hud.meter.doubleValue() - expected) < 0.001
            check(filename.removesuffix(".png"), True, 1)
            render(filename)
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
        hud.workspace_center.postNotificationName_object_(
            A.NSWorkspaceActiveSpaceDidChangeNotification, None
        )
        advance(0.05)
        check("space-change-keeps-dismissed-hud-hidden", False)
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
        blocker.orderOut_(None)
        hud.dispose()
        pump_events(app, timeout=0)
        (output / "result.json").write_text(json.dumps(records, indent=2) + "\n")
    focus_checks = sum("foreground_unchanged" in record for record in records)
    overlap_checks = sum(record["stage"] == "overlap" for record in records)
    print(f"Native HUD check passed: {focus_checks} WindowServer/focus checks, "
          f"{overlap_checks} overlap checks; dark rounded render.")
    print("Real app/Space/Stage Manager transitions, physical display hotplug and "
          "installed-service acceptance remain separate checks.")


if __name__ == "__main__":
    main()
