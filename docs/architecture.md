# Architecture and responsibilities

AGY Dictation is neither a speech model nor a separate authentication client. It is an experiment that controls the user's official AGY CLI interactively, retrieves the transcript from the CLI input field through an external-editor callback, and inserts it at the macOS cursor position.

```text
Global hotkey → macOS service / state control → Official AGY CLI in a PTY
                         ↓                               ↓
                     AppKit HUD                 Interactive voice input
                         ↑                               ↓
                  Processing result ← External-editor callback
                         ↓
               Native Unicode input events
                         ↓
                Current app's input position
```

## Module contracts

These modules and tools are currently implemented. Diagnostics are read-only, and builds only generate artifacts. The implementation files are the source of truth for function names, states, configuration keys, and entry points.

| Path | Responsibility |
| --- | --- |
| `src/agy_dictation/config.py` | Runtime paths and configuration |
| `src/agy_dictation/cli_backend.py` | Interactive sessions with the official CLI and Unix PTY transport |
| `src/agy_dictation/export_prompt.py` | Result delivery through the external-editor callback |
| `src/agy_dictation/macos/service.py` | Global hotkeys, target focus verification, cancellation, and native input |
| `src/agy_dictation/macos/engine.py` | Separate voice-engine process, microphone permissions, and CLI sessions |
| `src/agy_dictation/macos/hud.py` | Status display using Python, PyObjC, and AppKit |
| `src/agy_dictation/macos/menu.py` | Native status item, controls and settings window |
| `src/agy_dictation/settings.py` | Private preferences and migration from legacy shortcut presets |
| `src/agy_dictation/shortcuts.py` | Validated custom bindings and solo-modifier tap policy |
| `src/agy_dictation/macos/shortcut_editor.py` | Inline keycap recorder, preview, save/cancel and reset |
| `src/agy_dictation/macos/audio.py` | Input-device discovery and session-scoped native PCM capture |
| `src/agy_dictation/audio_stream.py` | Bounded loopback audio stream and meters from identical PCM |
| `src/agy_dictation/voice_session.py` | Capture/provider start, stop, finalization and cancellation ordering |
| `scripts/doctor.py` | Environment diagnostics |
| `scripts/build_macos.py` | Staging local app bundles and LaunchAgent metadata |
| `scripts/manage_macos.py` | Explicit installation, CLI setup, start, stop, and removal |

CLI terminal output and external-editor behavior may change between CLI versions. Do not describe this integration as a direct call to a stable public transcription API. Voice input is an interactive CLI TUI feature and cannot be replaced with `--print`. See the [official voice documentation](https://www.antigravity.google/docs/cli/commands/voice/).

The service and voice engine communicate through a local Unix socket. This transport also needs attention in a Windows port.

## Microphone and menu controls

The voice helper owns microphone capture. Each recording resolves either the system
default input or a saved device UID, and captures mono, signed 16-bit little-endian
PCM at 16 kHz. The selected device stays fixed for that recording. A disconnected
explicit selection fails rather than silently switching microphones. No system-wide
audio preference is changed.

The helper supplies a random loopback-only TCP address through `ANTIGRAVITY_MIC`,
using the CLI's documented external microphone route. The listener rejects clients
while disarmed and permits a single stream during a requested recording. Audio is
kept in a bounded in-memory queue, and RMS/peak values are computed from those same
bytes. A stalled stream is an error rather than an invitation to drop audio silently.
Stopping capture drains already queued native callbacks and pending sends before
provider finalization; cancellation invalidates the session and discards pending data.
The protocol has no provider acknowledgement for individual audio frames, so a
successful socket send is not recognition-completeness evidence.

Authenticated `audio-status` IPC returns device metadata and numeric meter values,
never audio or transcripts. The frontend polls on a background thread and marks stale
meters unavailable. The HUD normally shows the microphone name without a changing
volume judgement. The bar polls and renders at 30 Hz with 25 ms attack/100 ms release; warning
decisions use a separate, slower envelope. A 50 ms numeric peak window preserves
short syllables between polls without buffering their audio. Device/preference
metadata is cached for one second and refreshed when recording starts. TCP_NODELAY
prevents small audio sends from waiting on Nagle batching. A low
level must persist for eight seconds before a warning appears. Hysteresis and a
minimum display time prevent rapid warning toggles. These are input-level indicators,
not recognition-confidence scores. An AppKit tracking-mode timer keeps HUD expiry and meter updates
running while an NSMenu is open. Recording actions are dispatched only after menu
tracking returns, without activating the service or replacing the target app.

The small settings window is activated only when the user opens Settings. Preferences
are saved atomically; device/shortcut changes are disabled during capture/processing.
The login checkbox enables/disables only this service's installed LaunchAgent. It
does not stop the current recording or create another login item.

Shortcut editing captures AppKit key events only inside the settings window. The
frontend suspends its global trigger until editing finishes; Escape, window closure
and focus loss discard the draft. Changes are saved atomically only on explicit Save.
Modifier-only bindings are side-specific and trigger on release within 700 ms; another
key, modifier, click, scroll or a longer hold invalidates the tap. Regular chords retain
auto-repeat suppression. Turning the shortcut off keeps menu controls and Escape
cancellation available. Existing presets load without losing their binding.

## Required behavior

- The HUD must show recording and processing status without taking focus from the target app.
- Insert a successful result exactly once, without writing to the clipboard or switching apps to paste.
- Cancellation and errors must preserve existing text. Discard late callbacks from canceled sessions.
- Rapid restarts, repeated hotkeys, and process termination must not cause duplicate insertion or insertion of a previous session's result.
- Verify that retrieving a transcript does not submit it as a regular CLI agent prompt.

These are validation requirements, not evidence that the rebuilt repository meets them all. Sending input events alone does not prove that the target app accepted the text; verify the visible result as well.

## Input flow

Recording is not tied to the app where it began. When the user stops recording,
the service captures the current editable field and selection. It finishes the
provider session and saves the transcript even if that capture fails, so a missing
input field does not leave the microphone recording or discard a finished result.

Before typing, the service checks the captured field, original content, selection
and modifier keys once. It then sends paced Unicode keyboard chunks directly,
without clipboard use, whole-field setters or text/selection acknowledgement loops.
Between chunks it checks only cancellation, modifiers, user activity and target
identity. Keyboard and mouse-press events increment an in-memory activity counter;
keys, click positions and their contents are not retained. Our own Unicode events
carry a tag and do not increment that counter. A new user action or changed target
stops later chunks instead of sending them somewhere else.

A single optional text read after sending can remove the recovery file if it exactly
matches the expected result. Delayed or normalized text readback never blocks,
retries or reports a completed send as an insertion failure. The completion HUD
means dispatch finished; it is not a claim that every application has been tested.

The HUD paints a rounded dark background on a transparent, non-activating view.
Provider transcription remains in the CLI backend; it does not own focus, hotkeys,
mouse handling, text insertion or the HUD.

## Packaging boundaries

Local bundle builds depend on the existing Python framework and modules. Generating LaunchAgent metadata is separate from registering a service or enabling automatic startup. The build script does not install, register, or run anything. Only an explicit user invocation of the separate management tool with `--apply` installs files or changes services. Standalone distribution requires additional dependency packaging, signing, notarization, and validation on a clean Mac.
