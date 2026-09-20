"""Frontend preferences, bounded status polling and explicit login controls."""

import json
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path

METER_INTERVAL = 1 / 30
METER_FLOOR_DB = -40


def level_text(status):
    if not status:
        return "입력 상태 확인 중"
    if status.get("error") or status.get("selected_missing"):
        return "마이크 연결을 확인해 주세요"
    if not status.get("recording"):
        return "녹음할 때만 입력을 확인해요"
    return {
        "low": "입력 음량이 낮아요",
        "loud": "입력 음량이 너무 커요",
    }.get(status.get("warning"), "목소리에 따라 막대가 움직여요")


class InputFeedback:
    """Presentation only: smooth the meter and hold warnings across normal pauses."""

    def __init__(self):
        self.last = None
        self.level = 0.0
        self.warning_level = 0.0
        self.warning = None
        self.candidate = None
        self.candidate_since = 0.0
        self.changed_at = 0.0

    def update(self, status, now):
        value = status.copy()
        if not status.get("recording") or status.get("error") or status.get("selected_missing"):
            self.last = None
            self.level = 0.0
            self.warning_level = 0.0
            self.warning = self.candidate = None
            return {**value, "level": 0, "warning": None}
        first = self.last is None
        dt = min(0.5, max(0, now - self.last)) if not first else 0
        if self.last is None:
            self.candidate_since = self.changed_at = now
        self.last = now
        target = status.get("level", 0) if status.get("signal_present") else 0
        # The transport's -60..0 dB scale makes -42 dB background noise look 30% full.
        # Use a quieter-looking display floor only; do not gate PCM or change warnings.
        db = status.get("db", target * 60 - 60)
        display_target = (
            max(0.0, min(1.0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB))
            if status.get("signal_present") else 0.0
        )
        if first:
            self.level = display_target
            self.warning_level = target
        # Fast meter motion and slow warning decisions are separate signals.
        tau = 0.025 if display_target > self.level else 0.10
        self.level += (display_target - self.level) * (1 - math.exp(-dt / tau))
        # A missing stream must clear immediately; silence must settle to exactly zero.
        if not status.get("signal_present") or (display_target == 0 and self.level < 0.01):
            self.level = 0.0
        warning_tau = 0.16 if target > self.warning_level else 0.65
        self.warning_level += (target - self.warning_level) * (1 - math.exp(-dt / warning_tau))
        peak = status.get("peak", 0)
        if not status.get("signal_present"):
            candidate = None  # Missing/stale data is not a low-volume measurement.
        elif peak >= (0.85 if self.warning == "loud" else 0.98):
            candidate = "loud"
        elif self.warning_level < (0.30 if self.warning == "low" else 0.20):
            candidate = "low"
        else:
            candidate = None
        if candidate != self.candidate:
            self.candidate = candidate
            self.candidate_since = now
        delay = 8 if candidate == "low" else 0.8 if candidate == "loud" else 1
        if (
            candidate != self.warning and now - self.candidate_since >= delay
            and (self.warning is None or now - self.changed_at >= 2)
        ):
            self.warning = candidate
            self.changed_at = now
        return {**value, "level": self.level, "warning": self.warning}


class AudioStatus:
    def __init__(self, fetch):
        self.fetch = fetch
        self.value = {}
        self.updated = 0
        self.feedback = InputFeedback()
        self.closed = threading.Event()
        self.wakeup = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        while not self.closed.is_set():
            try:
                value = json.loads(self.fetch())
                if not isinstance(value, dict) or not isinstance(value.get("devices"), list):
                    raise ValueError("Invalid audio status")
                self.value = value
                self.updated = time.monotonic()
            except Exception:
                self.value = {}
            self.wakeup.wait(METER_INTERVAL if self.value.get("recording") else 1)
            self.wakeup.clear()

    def request_refresh(self):
        self.wakeup.set()

    def snapshot(self):
        value = self.value.copy()
        now = time.monotonic()
        if now - self.updated > 1:
            value.update(level=0, peak=0, signal_present=False)
        return self.feedback.update(value, now)

    def close(self):
        self.closed.set()
        self.wakeup.set()
        self.thread.join(timeout=2)


class LaunchAtLogin:
    def __init__(self, label):
        if re.fullmatch(r"[A-Za-z0-9.-]+", label) is None:
            raise ValueError("Invalid service label")
        self.label = label
        self.domain = f"gui/{os.getuid()}"
        self.target = f"{self.domain}/{label}"
        self.agent = Path.home() / "Library/LaunchAgents" / f"{label}.plist"

    def enabled(self):
        if not self.agent.is_file():
            return None
        result = subprocess.run(
            ["/bin/launchctl", "print-disabled", self.domain],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        match = re.search(r'"' + re.escape(self.label) + r'"\s*=>\s*(true|false)', result.stdout)
        return not (match and match.group(1) == "true")

    def set_enabled(self, enabled):
        if self.enabled() is None:
            raise ValueError("Service has not been installed")
        subprocess.run(
            ["/bin/launchctl", "enable" if enabled else "disable", self.target],
            capture_output=True,
            timeout=3,
            check=True,
        )
        if bool(self.enabled()) != enabled:
            raise RuntimeError("Login preference did not apply")
