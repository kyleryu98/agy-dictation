# Architecture and responsibilities

AGY Dictation is neither a speech model nor a separate authentication client. It is an experiment that controls the user's official AGY CLI interactively, retrieves the transcript from the CLI input field through an external-editor callback, and inserts it at the macOS cursor position.

```text
Global hotkey → macOS service / state control → Official AGY CLI in a PTY
                         ↓                               ↓
                     AppKit HUD                 Interactive voice input
                         ↑                               ↓
                  Processing result ← External-editor callback
                         ↓
           Native selected-text insertion
             or Unicode input events
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

## Input acknowledgement

When an editable target exposes a writable `AXSelectedText` and readable text,
the service verifies the captured selection and replaces it with the entire
transcript in one operation. It never sets the whole field's `AXValue`. Unsupported
targets retain bounded Unicode keyboard chunks. Capability is checked before any
write; an attempted native write is never retried through keyboard events, even if
AX returns an error, because the write may already have taken effect.

Text reads prefer `AXStringForRange` with a stable character count and matching
UTF-16 length, then fall back to `AXValue`. After writing, a single extra terminal
newline in the exposed value is tolerated. Missing text, missing spaces or existing
newlines, and internal line-break changes are not normalized away. Before any
subsequent keyboard chunk, both the original field's text and its caret must be
acknowledged while that field remains focused.

After the final write there are no more characters to send. Completion checks the
captured field directly, so switching to another app does not invalidate text that
has already arrived. An unconfirmed final write keeps the recovery transcript and
shows an uncertainty HUD, without a success sound, retry, or claim that insertion
definitely failed. This acknowledgement timeout is separate from provider
transcription finalization.

API references: Apple's [text range attribute](https://developer.apple.com/documentation/applicationservices/kaxstringforrangeparameterizedattribute)
and [Unicode keyboard event documentation](https://developer.apple.com/documentation/coregraphics/cgevent/keyboardsetunicodestring(stringlength:unicodestring:)).

## Packaging boundaries

Local bundle builds depend on the existing Python framework and modules. Generating LaunchAgent metadata is separate from registering a service or enabling automatic startup. The build script does not install, register, or run anything. Only an explicit user invocation of the separate management tool with `--apply` installs files or changes services. Standalone distribution requires additional dependency packaging, signing, notarization, and validation on a clean Mac.
