"""Non-activating macOS dictation HUD. All UI work runs on the main thread."""

import time
import math
import AppKit as A
import Foundation as F

ACTIVE = {"connecting", "recording", "transcribing", "inserting", "cancelling"}


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
        self.screen_layout = None
        style = A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel
        self.panel = PassivePanel.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(0, 0, 336, 88), style, A.NSBackingStoreBuffered, False
        )
        self.panel.setTitle_("AGY 음성입력")
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(A.NSColor.clearColor())
        self.panel.setHasShadow_(True)
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
        previous = self.kind
        self.kind = kind
        model = presentation(kind, detail)
        if model is None:
            if previous not in ACTIVE:
                self.hide()
            return
        title, subtitle, loading, color = model
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
        if not self.visible:
            if not self.place_on_screen(force=True):
                return
            self.panel.setAlphaValue_(1)
            self.panel.orderFrontRegardless()
            self.visible = True
        self.tick()

    def place_on_screen(self, force=False):
        screens = list(A.NSScreen.screens())
        if not screens:
            # Display reconfiguration can temporarily leave no available screen.
            self.screen_layout = None
            return False
        frames = [screen.visibleFrame() for screen in screens]
        layout = tuple(
            (r.origin.x, r.origin.y, r.size.width, r.size.height) for r in frames
        )
        if force or layout != self.screen_layout:
            screen = A.NSScreen.mainScreen()
            if screen not in screens:
                screen = screens[0]
            r = screen.visibleFrame()
            size = self.panel.frame().size
            self.panel.setFrameOrigin_(A.NSMakePoint(
                r.origin.x + max(0, (r.size.width - size.width) / 2),
                r.origin.y + min(20, max(0, r.size.height - size.height)),
            ))
            self.screen_layout = layout
        return True

    def tick(self):
        if not self.visible:
            return
        self.place_on_screen()
        now = time.monotonic()
        if self.kind == "recording":
            elapsed = int(now - self.started)
            self.timer.setStringValue_(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
            self.dot.layer().setOpacity_(0.65 + 0.35 * math.sin((now - self.started) * 3) ** 2)
        else:
            self.dot.layer().setOpacity_(1)
        if self.dismiss_at is not None:
            left = self.dismiss_at - now
            if left <= 0:
                self.hide()
            elif left < 0.2:
                self.panel.setAlphaValue_(left / 0.2)

    def hide(self):
        self.panel.orderOut_(None)
        self.visible = False
        self.dismiss_at = None
