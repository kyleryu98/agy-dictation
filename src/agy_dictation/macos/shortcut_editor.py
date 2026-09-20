"""Inline native shortcut recorder. Events stay in this settings window."""

import AppKit as A
import Foundation as F
from ..shortcuts import MODIFIER_KEYS, MODIFIER_SYMBOLS, SPECIAL_KEYS, Shortcut


def event_modifiers(event):
    flags = event.modifierFlags()
    return tuple(
        name
        for name, mask in (
            ("control", A.NSEventModifierFlagControl),
            ("option", A.NSEventModifierFlagOption),
            ("shift", A.NSEventModifierFlagShift),
            ("command", A.NSEventModifierFlagCommand),
        )
        if flags & mask
    )


class ShortcutCaptureView(A.NSView):
    def acceptsFirstResponder(self):
        return bool(getattr(self, "owner", None) and self.owner.enabled)

    def mouseDown_(self, event):
        self.owner.begin()

    def becomeFirstResponder(self):
        self.setNeedsDisplay_(True)
        return True

    def resignFirstResponder(self):
        self.setNeedsDisplay_(True)
        return True

    def accessibilityPerformPress(self):
        self.owner.begin()
        return True

    def keyDown_(self, event):
        owner = self.owner
        code, modifiers = event.keyCode(), event_modifiers(event)
        if not owner.editing:
            if code in (36, 49):
                owner.begin()
            return
        if event.isARepeat():
            return
        if code == 53 and not modifiers:
            owner.cancel()
            return
        if code == 36 and not modifiers and owner.draft is not None:
            owner.save()
            return
        if code == 48 and not modifiers:
            owner.cancel()
            self.window().selectNextKeyView_(None)
            return
        owner.pending_modifier = None
        text = SPECIAL_KEYS.get(code) or str(event.charactersIgnoringModifiers() or "").upper()
        owner.propose(code, modifiers, text)

    def flagsChanged_(self, event):
        owner = self.owner
        if not owner.editing:
            return
        code, modifiers = event.keyCode(), set(event_modifiers(event))
        if code not in MODIFIER_KEYS:
            return
        name, label = MODIFIER_KEYS[code]
        if modifiers == {name} and name in modifiers:
            owner.pending_modifier = code
            owner.preview = label
            owner.draft = None
            owner.refresh()
        elif not modifiers and owner.pending_modifier == code:
            owner.pending_modifier = None
            owner.propose(code, (), label)
        else:
            owner.pending_modifier = None

    def performKeyEquivalent_(self, event):
        if not self.owner.editing:
            return False
        self.keyDown_(event)
        return True

    def drawRect_(self, rect):
        owner = getattr(self, "owner", None)
        if owner is None:
            return
        path = A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            A.NSInsetRect(self.bounds(), 1, 1), 10, 10
        )
        A.NSColor.controlBackgroundColor().setFill()
        path.fill()
        focused = (
            self.window() is not None
            and self.window().isKeyWindow()
            and self.window().firstResponder() == self
        )
        color = (
            A.NSColor.controlAccentColor()
            if owner.editing or focused
            else A.NSColor.separatorColor()
        )
        color.setStroke()
        path.setLineWidth_(2 if owner.editing else 1)
        path.stroke()
        title = owner.preview if owner.editing else owner.current.label if owner.current else "끔"
        attributes = {
            A.NSFontAttributeName: A.NSFont.systemFontOfSize_weight_(13, A.NSFontWeightMedium),
            A.NSForegroundColorAttributeName: (
                A.NSColor.labelColor() if owner.enabled else A.NSColor.disabledControlTextColor()
            ),
        }
        F.NSString.stringWithString_(title).drawInRect_withAttributes_(
            A.NSMakeRect(14, 8, self.bounds().size.width - 28, 20), attributes
        )


class ShortcutActions(F.NSObject):
    def edit_(self, sender):
        self.owner.begin()

    def save_(self, sender):
        self.owner.save()

    def cancel_(self, sender):
        self.owner.cancel()

    def clear_(self, sender):
        self.owner.apply("disabled", None)

    def reset_(self, sender):
        self.owner.apply("ctrl_grave", None)


class ShortcutEditor:
    def __init__(self, apply, capture_changed):
        self.apply, self.capture_changed = apply, capture_changed
        self.current = self.draft = None
        self.editing = False
        self.enabled = True
        self.is_default = True
        self.pending_modifier = None
        self.monitor = None
        self.preview = "키를 눌러 주세요"
        self.actions = ShortcutActions.alloc().init()
        self.actions.owner = self
        self.view = A.NSView.alloc().initWithFrame_(A.NSMakeRect(142, 122, 312, 112))
        self.capture = ShortcutCaptureView.alloc().initWithFrame_(A.NSMakeRect(0, 68, 194, 36))
        self.capture.owner = self
        self.capture.setAccessibilityElement_(True)
        self.capture.setAccessibilityRole_(A.NSAccessibilityButtonRole)
        self.capture.setAccessibilityLabel_("녹음 단축키 변경")
        self.capture.setToolTip_("클릭한 다음 사용할 키를 직접 눌러 주세요")
        self.view.addSubview_(self.capture)
        self.edit = self.icon("pencil", "단축키 변경", "edit:", 202)
        self.clear = self.icon("trash", "단축키 끄기", "clear:", 239)
        self.reset = self.icon("arrow.counterclockwise", "기본 단축키 복원", "reset:", 276)
        self.hint = A.NSTextField.labelWithString_("")
        self.hint.setFrame_(A.NSMakeRect(0, 30, 312, 34))
        self.hint.setFont_(A.NSFont.systemFontOfSize_(11))
        self.hint.setTextColor_(A.NSColor.secondaryLabelColor())
        self.hint.setMaximumNumberOfLines_(2)
        self.hint.setLineBreakMode_(A.NSLineBreakByWordWrapping)
        self.hint.cell().setWraps_(True)
        self.hint.cell().setUsesSingleLineMode_(False)
        self.view.addSubview_(self.hint)
        self.cancel_button = self.button("취소", "cancel:", 156)
        self.save_button = self.button("저장", "save:", 238)
        self.save_button.setKeyEquivalent_("\r")
        self.refresh()

    def icon(self, symbol, title, action, x):
        button = A.NSButton.alloc().initWithFrame_(A.NSMakeRect(x, 69, 32, 34))
        button.setBordered_(False)
        button.setImage_(
            A.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
        )
        button.setToolTip_(title)
        button.setAccessibilityLabel_(title)
        button.setTarget_(self.actions)
        button.setAction_(action)
        self.view.addSubview_(button)
        return button

    def button(self, title, action, x):
        button = A.NSButton.buttonWithTitle_target_action_(title, self.actions, action)
        button.setFrame_(A.NSMakeRect(x, 0, 74, 28))
        self.view.addSubview_(button)
        return button

    def set_binding(self, binding, is_default):
        self.current, self.is_default = binding, is_default
        self.refresh()

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.cancel()
        self.refresh()

    def begin(self):
        if not self.enabled or self.editing:
            return
        self.editing = True
        self.draft = self.pending_modifier = None
        self.preview = "키를 눌러 주세요"
        # Run before AppKit's menu key equivalents (including Command-Q/W).
        # This is local to this app/window; it never monitors another app's typing.
        self.monitor = A.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            A.NSEventMaskKeyDown | A.NSEventMaskFlagsChanged, self.handle_event
        )
        self.capture_changed(True)
        self.capture.window().makeFirstResponder_(self.capture)
        self.hint.setStringValue_("키 조합이나 ⌘ 같은 보조 키를 누르세요. Esc로 취소할 수 있어요.")
        self.refresh()

    def handle_event(self, event):
        if not self.editing:
            return event
        window = self.capture.window()
        if window is None or not window.isKeyWindow():
            self.cancel()
            return event
        if event.type() == A.NSEventTypeFlagsChanged:
            self.capture.flagsChanged_(event)
        else:
            self.capture.keyDown_(event)
        return None

    def propose(self, code, modifiers, label):
        self.preview = " ".join([*(MODIFIER_SYMBOLS[m] for m in modifiers), label])
        try:
            self.draft = Shortcut.parse(
                {"key_code": code, "modifiers": modifiers, "key_label": label}
            )
        except ValueError as error:
            self.draft = None
            self.hint.setStringValue_(str(error))
        else:
            self.preview = self.draft.label
            self.hint.setStringValue_(
                "저장하면 바로 적용돼요. 다른 키를 눌러 다시 지정할 수 있어요."
            )
        self.refresh()

    def save(self):
        if self.editing and self.draft is not None:
            if self.apply("custom", self.draft.to_dict()):
                self.cancel()
            else:
                self.hint.setStringValue_("저장하지 못했어요. 다시 시도해 주세요.")

    def cancel(self):
        if self.editing:
            self.editing = False
            self.draft = self.pending_modifier = None
            if self.monitor is not None:
                A.NSEvent.removeMonitor_(self.monitor)
                self.monitor = None
            self.capture_changed(False)
        self.refresh()

    def refresh(self):
        self.capture.setNeedsDisplay_(True)
        self.edit.setEnabled_(self.enabled and not self.editing)
        self.clear.setEnabled_(self.enabled and not self.editing and self.current is not None)
        self.reset.setEnabled_(self.enabled and not self.editing and not self.is_default)
        self.cancel_button.setHidden_(not self.editing)
        self.save_button.setHidden_(not self.editing)
        self.save_button.setEnabled_(self.draft is not None)
        if not self.editing:
            self.hint.setStringValue_(
                "단축키가 꺼졌어요. 상단 메뉴로 녹음할 수 있어요."
                if self.current is None
                else "보조 키만 짧게 눌러 시작·완료해요. 다른 키와 함께 누르면 작동하지 않아요."
                if self.current.modifier_only
                else "한 번 눌러 시작하고, 다시 눌러 완료해요."
            )
