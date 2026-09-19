# Validation and acceptance

## Current evidence — 2026-09-19

| Check | Result | Scope |
| --- | --- | --- |
| Unit suite | 155 tests passed | Native frameworks, microphone and typing are mocked |
| Adapted installed frontend | 62 policy tests passed | Existing personal provider transport was retained; its three differing IPC client tests are excluded from this subset |
| Ruff and diff whitespace | Passed | Repository source |
| Native Quartz event tagging | Round-trip passed without posting events | Construction/readback only, not live keyboard delivery |
| Installed runtime | Source definitions matched; restarted into `idle` and stayed running | Startup, not speech-recognition acceptance |
| HUD transparency | Offscreen AppKit rendering had zero alpha at all four corners | Not a new screen-compositor test |
| Current natural-voice/app compatibility | Not yet accepted | Do not claim every Mac app or input field works |

The current insertion policy replaces earlier per-chunk acknowledgement experiments.
It binds the field when recording stops, checks it before writing, and sends Unicode
events without waiting for the editor to echo each chunk. User key presses, mouse
presses, cancellation and target changes stop later chunks. Tests cover this contract
rather than the removed text-reconciliation state machine.

The suite verifies that text/selection read counts remain constant for short and long
sentences, stale readback does not truncate delivery, selected text and emoji use
UTF-16 ranges, unrelated input interrupts delivery, and our own tagged events do not
interrupt it. It also covers changing apps during recording, target capture failure,
provider finalization failure, cancellation, missing/secure focus, browser focus
resolution, IPC, private files, packaging and installation boundaries.

The personal frontend was backed up before installation. It uses the repository's
current frontend with only runtime paths, its existing CLI transport and existing
signal control adapted. Provider account/login state and engine code were not
changed. The separately packaged repository version retains its own authenticated
IPC protocol.

## Earlier live findings that remain relevant

- On macOS 27.0 with Chrome 153.0.8010.52 and Python 3.13.3, actual Unicode input
  reached a single-line field, textarea and contenteditable with neighboring text
  and the clipboard preserved. These are observations of the previous frontend,
  not automatic certification of the current policy or other applications.
- Chrome advertised writable `AXSelectedText` and returned success without changing
  the field. That write path was removed. Do not reintroduce it based on capability
  or mock tests alone.
- A later natural-voice screenshot showed only the first 16 UTF-16 units and an
  incomplete-input HUD. Logs recorded a non-final text-readback timeout. This
  invalidated the earlier claim that the truncation issue was resolved.
- A speaker-to-physical-microphone attempt ran through recording, AGY transcription
  and insertion, but its text did not match the spoken test marker. It was not a
  successful recognition-accuracy or speech-tail test.
- A foreground-switch harness without a running Cocoa main loop cached the active
  app. Its switch results were discarded; native tests must run the main event loop.

## Automated development checks

Run from the repository root:

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check .
git diff --check
.venv/bin/python scripts/prepublish_check.py --check-identity
```

Automated tests must not record a microphone, type into real applications, request
permissions or change login state. Use synthetic strings and mocked native APIs.
A build stages artifacts only; it does not install or run a service. Environment
checks and a successful startup do not prove actual dictation.

## Manual acceptance procedure

Use a disposable document and a non-sensitive spoken sentence with a distinct final
word. Do not save speech, transcripts or unredacted logs in the repository.

1. Start recording with the normal shortcut and speak. Confirm the recording HUD.
2. While recording, move to the intended app and place its cursor between known
   surrounding text. Stop recording there. Confirm the entire sentence, final word,
   neighboring text, clipboard and lack of automatic message submission.
3. Repeat with plain text, multiline text, contenteditable, selected text, emoji,
   a longer utterance and the user's usual IME. Record actual pass/fail evidence.
4. Separately click or type after stopping but before insertion finishes. Verify
   that remaining input stops and is not redirected to the new location.
5. Switch apps immediately after input finishes. Completed delivery must not become
   an error merely because focus moved afterward.
6. Test Esc during recording and processing; missing input fields; denied capability;
   network/provider errors; and rapid repeated shortcuts. Preserve original text
   and recovery data on failure.

TextEdit, Chrome, Aside, Codex and other Electron/webview editors, Safari, Firefox,
and custom editable controls are compatibility targets. Empty/read-only surfaces and
password fields must not receive text. These targets are not claims of universal
support. Record environment versions, source revision, field type, pass/fail and
latency; keep real speech results separate from synthetic typing and unit tests.

Free-account quota, clean-Mac onboarding, fresh-login startup and Windows remain
separate acceptance gates. Windows support is not implemented.
