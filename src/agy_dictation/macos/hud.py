"""Non-activating macOS dictation HUD. All UI work runs on the main thread."""

import time
import AppKit as A
import Foundation as F
import Quartz as Q
from ..config import DISPLAY_NAME

ACTIVE = {"connecting", "recording", "transcribing", "inserting", "cancelling"}


def pump_events(app, timeout=0.1):
    """Dispatch AppKit events and commit window changes, including while hidden.

    A CFRunLoop alone does not drain NSApplication's event queue. In particular,
    display changes and deferred window updates must reach AppKit as well.
    Handle one event per turn so a busy queue cannot starve HUD deadlines.
    """
    event = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
        A.NSEventMaskAny,
        F.NSDate.dateWithTimeIntervalSinceNow_(timeout),
        F.NSDefaultRunLoopMode,
        True,
    )
    if event is not None:
        app.sendEvent_(event)
    app.updateWindows()


def rect_values(rect):
    return rect.origin.x, rect.origin.y, rect.size.width, rect.size.height


def interaction_screen(screens):
    """Prefer the foreground window's display without reading its text or using AX."""
    front = A.NSWorkspace.sharedWorkspace().frontmostApplication()
    windows = Q.CGWindowListCopyWindowInfo(
        Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
        Q.kCGNullWindowID,
    ) if front is not None else []
    for window in windows or []:
        if (
            window.get(Q.kCGWindowOwnerPID) != front.processIdentifier()
            or window.get(Q.kCGWindowLayer) != 0
        ):
            continue
        bounds = window.get(Q.kCGWindowBounds, {})
        if bounds.get("Width", 0) <= 0 or bounds.get("Height", 0) <= 0:
            continue
        # Quartz uses top-down coordinates relative to the menu-bar display.
        primary = screens[0].frame()
        x, width, height = bounds["X"], bounds["Width"], bounds["Height"]
        y = primary.origin.y + primary.size.height - bounds["Y"] - height

        def overlap(screen):
            sx, sy, sw, sh = rect_values(screen.frame())
            return max(0, min(x + width, sx + sw) - max(x, sx)) * max(
                0, min(y + height, sy + sh) - max(y, sy)
            )

        screen = max(screens, key=overlap)
        if overlap(screen) > 0:
            return screen
        break
    pointer = A.NSEvent.mouseLocation()
    for screen in screens:
        x, y, width, height = rect_values(screen.frame())
        if x <= pointer.x < x + width and y <= pointer.y < y + height:
            return screen
    return screens[0]


def presentation(kind, detail=""):
    if kind == "connecting":
        return ("마이크 연결 중", "잠시만 기다려 주세요", True, "blue")
    if kind == "recording":
        return ("녹음 중", "Ctrl + ₩로 완료 · Esc로 취소", False, "red")
    if kind == "transcribing":
        return ("글로 바꾸는 중", "잠시만 기다려 주세요", True, "blue")
    if kind == "cancelling":
        return ("취소 중", "입력하지 않고 녹음을 마쳐요", True, "gray")
    if kind == "inserting":
        return ("입력 중", "현재 커서에 넣고 있어요", True, "blue")
    if kind == "error":
        return ("입력이 중단됐어요", detail, False, "orange")
    if kind == "idle" and "취소" in detail:
        return ("취소했어요", "음성입력을 중단했어요", False, "gray")
    if kind == "idle" and detail == "입력 완료":
        return ("입력 완료", "", False, "green")
    return None


class LevelMeter(A.NSView):
    """A literal fill ratio with no progress animation or minimum filled end cap."""

    def setDoubleValue_(self, value):
        self.value = max(0.0, min(1.0, float(value)))
        self.setNeedsDisplay_(True)
        self.setAccessibilityValue_(f"{self.value:.0%}")

    def doubleValue(self):
        return getattr(self, "value", 0.0)

    def drawRect_(self, rect):
        bounds = self.bounds()
        radius = bounds.size.height / 2
        A.NSColor.secondaryLabelColor().colorWithAlphaComponent_(0.25).setFill()
        A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, radius, radius
        ).fill()
        value = self.doubleValue()
        if value > 0:
            width = bounds.size.width * value
            fill = A.NSMakeRect(bounds.origin.x, bounds.origin.y, width, bounds.size.height)
            A.NSColor.labelColor().setFill()
            A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                fill, min(radius, width / 2), radius
            ).fill()


class PassivePanel(A.NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class HUDBackground(A.NSView):
    """Paint only the rounded area; do not rely on backdrop-effect clipping."""

    def isOpaque(self):
        return False

    def drawRect_(self, rect):
        A.NSColor.clearColor().set()
        A.NSRectFillUsingOperation(self.bounds(), A.NSCompositingOperationCopy)
        A.NSColor.colorWithCalibratedWhite_alpha_(0.16, 0.97).setFill()
        A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 20, 20
        ).fill()


class Actions(F.NSObject):
    def cancel_(self, sender):
        self.callback()


def text_field(frame, size, color, bold=False):
    v = A.NSTextField.alloc().initWithFrame_(frame)
    v.setBezeled_(False)
    v.setDrawsBackground_(False)
    v.setEditable_(False)
    v.setSelectable_(False)
    v.setFont_(
        A.NSFont.systemFontOfSize_weight_(
            size, A.NSFontWeightSemibold if bold else A.NSFontWeightRegular
        )
    )
    v.setTextColor_(color)
    v.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
    return v


class DictationHUD:
    def __init__(self, cancel):
        self.kind = None
        self.started = 0.0
        self.dismiss_at = None
        self.visible = False
        self.pending_show = False
        self.last_state = None
        self.screen_layout = None
        self.shortcut_label = "Ctrl + ₩"
        style = A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel
        self.panel = PassivePanel.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(0, 0, 336, 88), style, A.NSBackingStoreBuffered, False
        )
        self.panel.setTitle_(DISPLAY_NAME)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(A.NSColor.clearColor())
        self.panel.setHasShadow_(True)
        # Our deadline/fade owns visibility; do not queue a second AppKit animation.
        self.panel.setAnimationBehavior_(A.NSWindowAnimationBehaviorNone)
        self.panel.setLevel_(A.NSFloatingWindowLevel)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setReleasedWhenClosed_(False)
        self.panel.setCollectionBehavior_(
            A.NSWindowCollectionBehaviorCanJoinAllSpaces
            | A.NSWindowCollectionBehaviorFullScreenAuxiliary
            | A.NSWindowCollectionBehaviorStationary
            | A.NSWindowCollectionBehaviorIgnoresCycle
        )
        self.panel.setAppearance_(A.NSAppearance.appearanceNamed_(A.NSAppearanceNameDarkAqua))
        view = HUDBackground.alloc().initWithFrame_(A.NSMakeRect(0, 0, 336, 88))
        self.panel.setContentView_(view)
        self.dot = A.NSView.alloc().initWithFrame_(A.NSMakeRect(20, 54, 10, 10))
        self.dot.setWantsLayer_(True)
        self.dot.layer().setCornerRadius_(5)
        view.addSubview_(self.dot)
        self.title = text_field(A.NSMakeRect(44, 45, 195, 24), 15, A.NSColor.whiteColor(), True)
        view.addSubview_(self.title)
        self.subtitle = text_field(
            A.NSMakeRect(44, 23, 270, 20), 11.5, A.NSColor.colorWithWhite_alpha_(0.72, 1)
        )
        view.addSubview_(self.subtitle)
        self.timer = text_field(
            A.NSMakeRect(239, 46, 46, 21), 12, A.NSColor.colorWithWhite_alpha_(0.8, 1)
        )
        self.timer.setFont_(
            A.NSFont.monospacedDigitSystemFontOfSize_weight_(12, A.NSFontWeightRegular)
        )
        self.timer.setAlignment_(A.NSTextAlignmentRight)
        view.addSubview_(self.timer)
        self.progress = A.NSProgressIndicator.alloc().initWithFrame_(A.NSMakeRect(46, 12, 264, 5))
        self.progress.setStyle_(A.NSProgressIndicatorStyleBar)
        self.progress.setIndeterminate_(True)
        self.progress.setUsesThreadedAnimation_(True)
        self.progress.setControlSize_(A.NSControlSizeSmall)
        view.addSubview_(self.progress)
        self.meter = LevelMeter.alloc().initWithFrame_(A.NSMakeRect(46, 12, 264, 3))
        self.meter.setAccessibilityLabel_("마이크 입력 음량")
        self.meter.setHidden_(True)
        view.addSubview_(self.meter)
        self.actions = Actions.alloc().init()
        self.actions.callback = cancel
        self.close = A.NSButton.alloc().initWithFrame_(A.NSMakeRect(297, 44, 24, 26))
        self.close.setTitle_("×")
        self.close.setFont_(A.NSFont.systemFontOfSize_(21))
        self.close.setBordered_(False)
        self.close.setTarget_(self.actions)
        self.close.setAction_("cancel:")
        self.close.setToolTip_("녹음 취소 (Esc)")
        self.close.setAccessibilityLabel_("녹음 취소")
        view.addSubview_(self.close)

    def update(self, kind, detail=""):
        state = (kind, detail)
        if state == self.last_state:
            self.tick()
            return
        self.last_state = state
        previous = self.kind
        self.kind = kind
        model = presentation(kind, detail)
        if model is None:
            self.hide()
            return
        title, subtitle, loading, color = model
        if kind == "recording":
            subtitle = self.shortcut_hint()
        colors = {
            "red": A.NSColor.systemRedColor(),
            "blue": A.NSColor.systemBlueColor(),
            "green": A.NSColor.systemGreenColor(),
            "orange": A.NSColor.systemOrangeColor(),
            "gray": A.NSColor.systemGrayColor(),
        }
        self.dot.layer().setBackgroundColor_(colors[color].CGColor())
        self.title.setStringValue_(title)
        self.subtitle.setStringValue_(subtitle)
        self.subtitle.setToolTip_(subtitle)
        self.progress.setHidden_(not loading)
        self.meter.setHidden_(kind != "recording")
        self.progress.setIndeterminate_(loading)
        if kind == "recording":
            self.meter.setDoubleValue_(0)
        if loading:
            self.progress.startAnimation_(None)
        else:
            self.progress.stopAnimation_(None)
        self.timer.setHidden_(kind != "recording")
        self.close.setHidden_(kind not in ACTIVE)
        if kind == "recording" and previous != "recording":
            self.started = time.monotonic()
        self.dismiss_at = (
            None if kind in ACTIVE else time.monotonic() + (
                6 if kind == "error" else 0.85
            )
        )
        # A new session/status may interrupt the previous toast's fade.
        self.panel.setAlphaValue_(1)
        if previous not in ACTIVE or kind == "transcribing":
            self.screen_layout = None
        self.pending_show = not self.visible
        self.tick()

    def place_on_screen(self, force=False):
        screens = list(A.NSScreen.screens())
        if not screens:
            # Display reconfiguration can temporarily leave no available screen.
            self.screen_layout = None
            return False
        layout = tuple(
            (
                screen.deviceDescription()["NSScreenNumber"],
                rect_values(screen.frame()),
                rect_values(screen.visibleFrame()),
                screen.backingScaleFactor(),
            ) for screen in screens
        )
        if force or layout != self.screen_layout:
            screen = interaction_screen(screens)
            r = screen.visibleFrame()
            size = self.panel.frame().size
            self.panel.setFrameOrigin_(A.NSMakePoint(
                r.origin.x + max(0, (r.size.width - size.width) / 2),
                r.origin.y + min(20, max(0, r.size.height - size.height)),
            ))
            self.screen_layout = layout
        return True

    def tick(self):
        now = time.monotonic()
        # Expiry must not depend on a display being available during hotplug.
        if self.dismiss_at is not None and now >= self.dismiss_at:
            self.hide()
            return
        if not self.visible and not self.pending_show:
            return
        if not self.place_on_screen(force=self.pending_show):
            return
        if self.pending_show:
            self.panel.orderFrontRegardless()
            self.visible = True
            self.pending_show = False
        if self.kind == "recording":
            elapsed = int(now - self.started)
            self.timer.setStringValue_(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        self.dot.layer().setOpacity_(1)
        if self.dismiss_at is not None:
            left = self.dismiss_at - now
            if left < 0.2:
                self.panel.setAlphaValue_(left / 0.2)

    def set_audio_status(self, audio):
        if self.kind != "recording":
            return
        from .controls import level_text

        self.meter.setDoubleValue_(audio.get("level", 0))
        name = audio.get("input_name", "")
        warning = audio.get("warning") or audio.get("error") or audio.get("selected_missing")
        text = f"{name} · {level_text(audio)}" if name and warning else name or level_text(audio)
        self.subtitle.setStringValue_(text)
        self.subtitle.setToolTip_(text + " · " + self.shortcut_hint())

    def shortcut_hint(self):
        shortcut = getattr(self, "shortcut_label", "Ctrl + ₩")
        return f"{shortcut}로 완료 · Esc로 취소" if shortcut else "상단 메뉴에서 완료 · Esc로 취소"

    def hide(self):
        self.progress.stopAnimation_(None)
        self.panel.orderOut_(None)
        self.visible = False
        self.pending_show = False
        self.dismiss_at = None
