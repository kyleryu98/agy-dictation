"""Native menu-bar controls and a small settings window; no recording at import."""

from collections import deque
import AppKit as A
import Foundation as F
from .controls import level_text
from .hud import ACTIVE
from .shortcut_editor import ShortcutEditor
from ..config import DISPLAY_NAME

STATES = {
    "starting": "준비 중",
    "idle": "대기 중",
    "connecting": "마이크 연결 중",
    "recording": "녹음 중",
    "transcribing": "글로 바꾸는 중",
    "inserting": "입력 중",
    "cancelling": "취소 중",
    "error": "확인 필요",
    "permission_required": "권한 확인 필요",
}


def label(text, x, y, width, size=13):
    view = A.NSTextField.labelWithString_(text)
    view.setFrame_(A.NSMakeRect(x, y, width, 24))
    view.setFont_(A.NSFont.systemFontOfSize_(size))
    return view


class MenuActions(F.NSObject):
    def toggle_(self, sender):
        self.owner.pending.append("toggle")

    def cancel_(self, sender):
        self.owner.pending.append("cancel")

    def restart_(self, sender):
        self.owner.pending.append("restart")

    def quit_(self, sender):
        self.owner.pending.append("quit")

    def settings_(self, sender):
        self.owner.pending.append("settings")

    def microphone_(self, sender):
        self.owner.change(microphone_uid=sender.representedObject() or None)

    def microphonePopup_(self, sender):
        self.microphone_(sender.selectedItem())

    def windowDidResignKey_(self, notification):
        self.owner.shortcut_editor.cancel()

    def windowWillClose_(self, notification):
        self.owner.shortcut_editor.cancel()

    def sounds_(self, sender):
        self.owner.change(sounds=bool(sender.state()))

    def login_(self, sender):
        try:
            self.owner.login.set_enabled(bool(sender.state()))
            self.owner.feedback.setStringValue_("자동 실행 설정을 저장했어요")
        except Exception:
            self.owner.feedback.setStringValue_("자동 실행 설정을 저장하지 못했어요")
        self.owner.refresh_login()

    def menuWillOpen_(self, menu):
        self.owner.tracking = True

    def menuDidClose_(self, menu):
        self.owner.tracking = False


class MenuBar:
    def __init__(self, settings, login, dispatch, changed, capture_changed=lambda active: None):
        self.settings, self.login = settings, login
        self.dispatch, self.changed = dispatch, changed
        self.capture_changed = capture_changed
        self.kind = "starting"
        self.audio = {}
        self.pending = deque()
        self.tracking = False
        self.device_signature = None
        self.icon_kind = None
        self.window = None
        self.login_available = False
        self.actions = MenuActions.alloc().init()
        self.actions.owner = self
        self.item = A.NSStatusBar.systemStatusBar().statusItemWithLength_(
            A.NSVariableStatusItemLength
        )
        self.item.setAutosaveName_("ProListenDictation")
        icon = A.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "mic", f"{DISPLAY_NAME} 음성입력"
        )
        if icon is not None:
            icon.setTemplate_(True)
            self.item.button().setImage_(icon)
        else:
            self.item.button().setTitle_("음성")
        self.menu = A.NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self.actions)
        self.status = self.add(f"{DISPLAY_NAME} · 준비 중")
        self.microphone = self.add("마이크 확인 중")
        self.signal = self.add("녹음할 때 입력 음량을 표시해요")
        self.menu.addItem_(A.NSMenuItem.separatorItem())
        self.toggle = self.add("녹음 시작", "toggle:")
        self.cancel = self.add("녹음 취소", "cancel:")
        self.menu.addItem_(A.NSMenuItem.separatorItem())
        self.input_menu = A.NSMenu.alloc().init()
        self.input_menu.setAutoenablesItems_(False)
        self.input_item = self.add("마이크 선택")
        self.input_item.setSubmenu_(self.input_menu)
        self.configure = self.add("설정…", "settings:")
        self.restart = self.add("음성 엔진 다시 시작", "restart:")
        self.menu.addItem_(A.NSMenuItem.separatorItem())
        self.quit = self.add(f"{DISPLAY_NAME} 종료", "quit:")
        self.item.setMenu_(self.menu)

    def add(self, title, action=None, menu=None):
        item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        item.setTarget_(self.actions)
        item.setEnabled_(action is not None)
        (self.menu if menu is None else menu).addItem_(item)
        return item

    def change(self, **changes):
        if self.kind in ACTIVE or self.kind == "starting":
            return False
        saved = False
        try:
            preference = self.settings.change(**changes)
            self.changed(preference)
            saved = True
            self.device_signature = None
            if self.window is not None:
                self.feedback.setStringValue_("설정을 저장했어요 · 다음 녹음부터 적용돼요")
        except Exception:
            if self.window is not None:
                self.feedback.setStringValue_("설정을 저장하지 못했어요")
        self.sync_settings()
        return saved

    def update(self, kind, audio):
        self.kind, self.audio = kind, audio
        active = kind in ACTIVE or kind == "starting"
        editing = self.window is not None and self.shortcut_editor.editing
        title = STATES.get(kind, "상태 확인 중")
        self.status.setTitle_(f"{DISPLAY_NAME} · {title}")
        self.item.button().setToolTip_(f"{DISPLAY_NAME} · {title}")
        self.item.button().setAppearsDisabled_(kind == "error")
        if kind != self.icon_kind:
            symbol = (
                "mic.fill"
                if kind == "recording"
                else "ellipsis.bubble"
                if kind in ACTIVE
                else "mic.slash"
                if kind in ("error", "permission_required")
                else "mic"
            )
            icon = A.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
            if icon is not None:
                icon.setTemplate_(True)
                self.item.button().setImage_(icon)
            self.item.button().setContentTintColor_(
                A.NSColor.systemRedColor() if kind == "recording" else None
            )
            self.icon_kind = kind
        name = audio.get("input_name") if audio.get("recording") else audio.get("selected_name")
        self.microphone.setTitle_("마이크: " + (name or "확인 중"))
        self.signal.setTitle_(level_text(audio))
        self.toggle.setTitle_("녹음 완료" if kind == "recording" else "녹음 시작")
        self.toggle.setEnabled_(kind in ("idle", "error", "recording") and not editing)
        self.cancel.setEnabled_(kind in ACTIVE)
        self.configure.setEnabled_(not active)
        self.input_item.setEnabled_(not active and not editing)
        self.restart.setEnabled_(not active and not editing)
        # Never discard a running utterance just because the menu was clicked.
        self.quit.setEnabled_(not active)
        self.refresh_devices()
        if self.window is not None:
            self.signal_label.setStringValue_(level_text(audio))
            self.level.setDoubleValue_(audio.get("level", 0) if kind == "recording" else 0)
            self.shortcut_editor.set_enabled(not active)
            for control in (self.input_popup, self.sounds):
                control.setEnabled_(not active and not editing)
            self.autostart.setEnabled_(self.login_available and not active and not editing)

    def shortcut_capture_changed(self, active):
        self.capture_changed(active)
        self.update(self.kind, self.audio)

    def refresh_devices(self):
        devices = self.audio.get("devices", [])
        selected = self.settings.value.microphone_uid
        signature = (tuple((d["uid"], d["name"]) for d in devices), selected)
        if signature == self.device_signature or self.tracking:
            return
        self.device_signature = signature
        self.input_menu.removeAllItems()
        choices = [(None, "Mac의 기본 마이크 따르기")] + [(d["uid"], d["name"]) for d in devices]
        if selected is not None and selected not in {uid for uid, _ in choices}:
            choices.append((selected, "선택한 마이크 · 연결 끊김"))
        for uid, name in choices:
            item = self.add(name, "microphone:", self.input_menu)
            item.setRepresentedObject_(uid or "")
            item.setState_(int(uid == selected))
        if self.window is not None:
            self.input_popup.removeAllItems()
            for index, (uid, name) in enumerate(choices):
                self.input_popup.addItemWithTitle_(name)
                self.input_popup.itemAtIndex_(index).setRepresentedObject_(uid or "")
                if uid == selected:
                    self.input_popup.selectItemAtIndex_(index)

    def perform_pending(self):
        # Called after menu tracking has returned, preserving the input app's focus.
        if self.tracking:
            return
        while self.pending:
            action = self.pending.popleft()
            if action == "settings":
                self.show_settings()
            else:
                self.dispatch(action)

    def show_settings(self, activate=True):
        if self.kind in ACTIVE or self.kind == "starting":
            return
        if self.window is None:
            self.build_settings()
        self.device_signature = None
        self.refresh_devices()
        self.sync_settings()
        self.refresh_login()
        if activate:
            A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
        else:
            self.window.orderFrontRegardless()

    def build_settings(self):
        self.window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(0, 0, 480, 430),
            A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
            A.NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_(f"{DISPLAY_NAME} 설정")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self.actions)
        self.window.center()
        view = self.window.contentView()
        for text, x, y, width, size in (
            ("음성입력을 편하게", 24, 380, 420, 21),
            ("사용할 마이크와 단축키를 설정하세요.", 24, 353, 420, 12),
            ("마이크", 24, 308, 110, 13),
            ("입력 음량", 24, 266, 110, 13),
            ("녹음 단축키", 24, 197, 110, 13),
        ):
            view.addSubview_(label(text, x, y, width, size))
        self.input_popup = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            A.NSMakeRect(142, 305, 312, 28), False
        )
        self.input_popup.setTarget_(self.actions)
        self.input_popup.setAction_("microphonePopup:")
        view.addSubview_(self.input_popup)
        self.level = A.NSProgressIndicator.alloc().initWithFrame_(A.NSMakeRect(146, 273, 304, 8))
        self.level.setIndeterminate_(False)
        self.level.setMinValue_(0)
        self.level.setMaxValue_(1)
        view.addSubview_(self.level)
        self.signal_label = label("녹음할 때만 입력을 확인해요", 142, 241, 312, 11)
        view.addSubview_(self.signal_label)
        self.shortcut_editor = ShortcutEditor(
            lambda mode, binding: self.change(shortcut=mode, custom_shortcut=binding),
            self.shortcut_capture_changed,
        )
        view.addSubview_(self.shortcut_editor.view)
        self.sounds = A.NSButton.checkboxWithTitle_target_action_(
            "녹음 시작·완료 소리", self.actions, "sounds:"
        )
        self.sounds.setFrame_(A.NSMakeRect(142, 83, 300, 24))
        view.addSubview_(self.sounds)
        self.autostart = A.NSButton.checkboxWithTitle_target_action_(
            "로그인할 때 자동 실행", self.actions, "login:"
        )
        self.autostart.setFrame_(A.NSMakeRect(142, 49, 300, 24))
        view.addSubview_(self.autostart)
        self.feedback = label("설정은 이 Mac에 저장돼요", 24, 12, 432, 11)
        view.addSubview_(self.feedback)

    def sync_settings(self):
        if self.window is not None:
            self.shortcut_editor.set_binding(
                self.settings.value.binding, self.settings.value.shortcut == "ctrl_grave"
            )
            self.sounds.setState_(int(self.settings.value.sounds))

    def refresh_login(self):
        try:
            enabled = self.login.enabled()
        except Exception:
            enabled = None
        self.login_available = enabled is not None
        self.autostart.setEnabled_(enabled is not None)
        self.autostart.setState_(int(bool(enabled)))
        if enabled is None:
            self.autostart.setToolTip_("설치된 서비스에서 설정할 수 있어요")

    def close(self):
        if self.window is not None:
            self.shortcut_editor.cancel()
            self.window.orderOut_(None)
        A.NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
