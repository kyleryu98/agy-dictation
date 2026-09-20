# Validation and acceptance

## Current evidence — 2026-09-20

| Check | Result | Scope |
| --- | --- | --- |
| Unit suite | 208 tests passed | Native capture, microphone and typing are mocked; transport tests use synthetic PCM on loopback |
| Native custom shortcuts | Save/Enter, cancel, side-specific modifier, disable and reset passed | Synthetic AppKit events inside the settings window; global trigger suspension and modifier/chord separation are policy-tested |
| Native menu/settings | Passed | Real AppKit controls with synthetic devices, private test preferences and fake login controls; no microphone, input listeners or login change |
| Meter responsiveness | 30 Hz polling/rendering; 20 synthetic timing phases improved median time to 90% from 352.5 ms to 49.5 ms | Scheduling model only; excludes hardware, IPC, compositor and provider latency |
| Calm input feedback | Normal syllables/pauses keep a fixed label; low-volume warning waits eight seconds; isolated peaks do not flash warnings | Presentation smoothing only; no audio/provider changes |
| Native PCM extraction | Exact bytes matched | Synthetic CoreMedia audio sample; no real capture |
| Audio transport | Exact meter/provider bytes and final-buffer drain passed | Synthetic capture; device loss, failure, stale meter and previous-session callbacks covered |
| Installed menu/audio helper | Started in `idle`; menu status item visible; input-device status responded | Tested source hashes matched; selected default microphone found; capture inactive at rest; no restart traceback |
| Distribution packaging | Wheel and source distribution built; artifact privacy scan passed | Build artifacts only, not a standalone macOS release |
| New capture path with real speech | Not yet accepted | Synthetic PCM checks do not prove AGY recognition or spoken tail completeness |
| HUD regressions against the previous source | Four test groups failed, with seven failing cases; fixed source passes | Interrupted fade, repeated terminal status, ready-state dismissal and showing during display reconfiguration |
| Native HUD lifecycle | 15 WindowServer/foreground checks passed | Isolated repository HUD; recording, processing, interrupted fade, repeated completion, cancellation, ready and error dismissal |
| HUD render | Dark rounded fill and transparent corners passed | Offscreen render of the synthetic HUD only; no desktop capture |
| Display geometry | Mocked placement checks passed | Left/right/above/below displays, spanning windows, disconnect, empty display list, scale, display identity and Dock changes |
| Ruff and diff whitespace | Passed | Repository source |
| Installed runtime with these HUD changes | Applied with explicit user authorization; restarted into fresh `idle` | Source hashes matched the tested candidate; old/new service had no onscreen HUD windows; no restart traceback |
| Physical display disconnect/reconnect | Not yet accepted | Geometry tests and a single connected display do not prove physical hotplug |
| Current natural-voice/app compatibility | Not yet accepted | Do not claim every Mac app or input field works |

The installed service was inspected read-only. Its status file said the HUD was
hidden, while WindowServer still listed a visible panel at roughly 44% opacity
and coordinates from the previous display. The old loop ran Core Foundation but
did not dispatch AppKit events or call `updateWindows`. The repository now pumps
AppKit events even while the HUD is hidden, with a bounded wait and an autorelease
pool. It also acknowledges only the status snapshot actually rendered, so a worker
update during rendering cannot silently skip completion.

The isolated native check initially found that AppKit's automatic window animation
kept a dismissed panel in WindowServer's onscreen list after the HUD's own fade.
Disabling that additional animation made the native checks pass. New states restore
full opacity, terminal messages keep their original deadline, and a ready state
clears active UI. A pending show recovers when displays return without reviving an
expired completion message. Screen selection uses the foreground window's bounds,
then the pointer as a fallback; it reads no window titles or editable content.
Window metadata is queried only when placing or relocating the panel, not each tick.

The native checks do not start the service, connect to a provider, record sound,
install input listeners, type into applications, request permissions or change the
installed copy. They verify foreground focus remains unchanged. The older tests
only inspected Python state and mocked coordinates, which could pass while the
actual macOS panel remained visible.

The menu/settings check exercises actual AppKit controls, preference persistence,
recording-time settings locks and menu-action deferral. The passive menu preserves
foreground focus; opening Settings deliberately gives that window keyboard focus.
The HUD remains non-key during editing and recording. A
native CoreMedia sample built from synthetic PCM verifies the callback extraction
format. The actual settings window was visually inspected through WindowServer
capture; offscreen view caching does not render every modern native control reliably.
Audio tests verify that the same bytes produce the meter and reach the socket, queued
tail buffers drain at stop, and callbacks from an earlier recording cannot enter a
later session. They never open a physical or virtual microphone.

The subsequent installed menu/settings update was backed up before application. Its
personal CLI/editor transport and input-insertion functions were preserved; the voice
helper gained the capture coordinator and numeric/device status endpoint. Startup
verification found the menu-bar item visible, the selected default device available,
and capture inactive while idle. No physical/virtual microphone was opened for the
automated checks. Real speech recognition and stop-time tail completeness through
this new capture path remain manual acceptance gates.

After the user explicitly authorized application and restart, both installed frontend
files were backed up and replaced. The personal provider transport, input delivery,
paths and signal controls were preserved; AST comparison limited service changes to
the HUD import, AppKit startup and UI loop. The new LaunchAgent process reached
`idle`, reported a hidden non-key HUD, and WindowServer no longer listed the old or
new service's panel. This is installation/startup evidence, not a physical display
hotplug or speech-to-input acceptance result.

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

On 2026-09-19, the personal frontend was backed up before installation. It used the
then-current frontend with only runtime paths, its existing CLI transport and existing
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

The separate opt-in native HUD check briefly shows a non-activating synthetic HUD:

```sh
.venv/bin/python scripts/check_hud.py
.venv/bin/python scripts/check_menu.py
```

It compares Python state, AppKit visibility and the actual WindowServer window
list, and writes synthetic HUD evidence under the ignored `work/hud-check/` folder.
Keep the foreground application unchanged during the check. This is not a speech
or installed-service test. The event loop and animation behavior follow Apple's
[window-update documentation](https://developer.apple.com/documentation/appkit/nsapplication/updatewindows())
and [automatic-animation documentation](https://developer.apple.com/documentation/appkit/nswindow/animationbehavior-swift.property).

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
7. Connect and disconnect an external display while idle, recording, processing and
   showing completion. Repeat with changed primary display, scale, display placement
   and Dock position. Confirm the HUD stays inside the active display's usable frame,
   keeps its dark background and disappears about 0.85 seconds after completion or
   cancellation (six seconds for errors). Start another recording during the fade;
   its opacity must return to full and the previous deadline must not hide it.

TextEdit, Chrome, Aside, Codex and other Electron/webview editors, Safari, Firefox,
and custom editable controls are compatibility targets. Empty/read-only surfaces and
password fields must not receive text. These targets are not claims of universal
support. Record environment versions, source revision, field type, pass/fail and
latency; keep real speech results separate from synthetic typing and unit tests.

Free-account quota, clean-Mac onboarding, fresh-login startup and Windows remain
separate acceptance gates. Windows support is not implemented.
