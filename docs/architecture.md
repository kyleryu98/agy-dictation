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
| `scripts/doctor.py` | Environment diagnostics |
| `scripts/build_macos.py` | Staging local app bundles and LaunchAgent metadata |
| `scripts/manage_macos.py` | Explicit installation, CLI setup, start, stop, and removal |

CLI terminal output and external-editor behavior may change between CLI versions. Do not describe this integration as a direct call to a stable public transcription API. Voice input is an interactive CLI TUI feature and cannot be replaced with `--print`. See the [official voice documentation](https://www.antigravity.google/docs/cli/commands/voice/).

The service and voice engine communicate through a local Unix socket. This transport also needs attention in a Windows port.

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
