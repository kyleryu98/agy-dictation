# Validation procedures and evidence limits

## Available evidence

| Subject | Evidence | Limitation |
| --- | --- | --- |
| Original personal implementation | Three real speech-to-TextEdit insertions, with focus and clipboard preserved | Results from the original personal environment |
| Cancellation in the original personal implementation | Approximately 0.2 seconds observed when canceling during recording or processing | Not a performance guarantee or regression-test metric |
| Rebuilt repository | Clean-Mac installation and end-to-end retesting are incomplete | Cannot inherit the earlier implementation's successful results |
| Voice on a free personal account | Not tested with an actual account | Distinguish official availability guidance from measured results |
| Windows | Not implemented | No basis for claiming passing tests or support |

Earlier results are observations provided in the project handoff. No audio was recorded, and no service, account, or automatic-startup settings were changed while writing this document.

## Local development checks

The following commands are intended to be run from the repository root. Listing a command here is not evidence that it has run successfully.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/build_macos.py --output dist
```

Check the number of discovered unit tests, failures, and skipped tests. Tests that do not exercise the real microphone, permissions, external service, and target app cannot establish successful dictation. Diagnostics check the environment; they do not verify actual CLI voice eligibility or successful native insertion.

A build stages the local app and LaunchAgent metadata. It must not install, register, or run the app. Inspect the artifacts' Python paths, module dependencies, and possible personal data, but do not publish artifacts containing local paths.

## Manual end-to-end testing

Run this procedure once the rebuilt version's installation and startup procedure is established, in an environment where the tester has personally completed official CLI onboarding and macOS permission setup. Use synthetic speech examples only. Do not add real transcripts or runtime logs to the repository.

1. Put baseline text in a TextEdit test document and position the cursor. Set the clipboard to a non-sensitive baseline value.
2. Start recording with Control + grave/backtick or Control + backslash. Verify that the HUD appears and TextEdit retains focus.
3. Speak a short example containing both Korean and English, then stop with the same hotkey. Check the processing indicator, actual inserted text, and preservation of the clipboard and focus. Record at least three independent runs.
4. Test Esc during recording and during processing separately. Verify that existing text remains intact and delayed results are not inserted. Record the actual latency in this environment rather than reusing the earlier observation of approximately 0.2 seconds.
5. Test empty speech, long sentences, Unicode and emoji, IME composition, selected text, rapid restarts, and repeated hotkeys. Verify that failures and cancellation do not delete selected text.
6. Separately test moving to another input position or switching apps during processing. Check the actual insertion target and whether failures are handled safely.
7. Test network failure, CLI termination, missing or delayed callbacks, and denied permissions. Check for unintended prompt submission, duplicate insertion, and leftover processes or temporary data.

This is guidance for future testing. Running the build script does not perform these manual tests.

### macOS input compatibility matrix

Repeat the insertion, selection-preservation, cancellation and focus-change cases for
each row. These rows are acceptance targets, not claims of verified compatibility.

| Target | Input cases | Current repository evidence |
| --- | --- | --- |
| TextEdit | Plain and rich text, cursor in existing text | Synthetic focus tests only |
| Chrome | HTML input/textarea, contenteditable, nested iframe, long page | User reported one successful voice insertion; broader matrix unverified |
| Aside Browser | HTML input/textarea, contenteditable, nested iframe, long page | Synthetic focus tests only |
| Safari and Firefox | HTML input/textarea and contenteditable | Not tested in these browsers |
| Codex, Claude and Signal | Electron/webview composer, cold app start | Synthetic focus tests only |
| Custom editors | Editable group or writable selected-text capability | Synthetic capability tests only |
| Non-input surfaces | Page body, read-only/disabled fields, password fields | Must refuse unsafe insertion |

For browsers, test both a fresh launch before any accessibility inspector is used and
an already-running session. The service now requests accessibility-tree activation
on demand for any application, follows nested focus references and editable ancestors,
and searches the focused window as well as the app's reported focus. A system-wide
focus result is accepted only when its process matches the target application.
Search remains bounded; an application that exposes no identifiable editable focus
will still be rejected. Do not replace this check with blind typing or clipboard paste.

## Recording results

Record the macOS, Python, and AGY CLI versions; repository revision or source-snapshot identifier; account plan type without the email address; target app; test case; pass, fail, or untested status; and observed latency. Keep the original implementation's results separate from the rebuilt version's results, unit tests separate from real voice input, and local bundle checks separate from execution on a clean Mac. Leave the exact Gemini Audio model version marked as unconfirmed.

## 2026-09-19 repository cleanup validation

- 14 automated tests passed on this Mac. These tests do not use the real microphone or generate user input.
- Ruff passed.
- sdist and wheel builds succeeded.
- The development macOS app and LaunchAgent metadata were generated, and ad-hoc signature verification passed.
- Installation, service registration, and clean-Mac end-to-end testing were not performed. The existing running service was not changed.

## 2026-09-19 pre-publication security regression checks

65 tests passed. Coverage includes link and file replacement, incorrect ownership and permissions, stale or duplicate callbacks, cancellation cleanup, IPC authentication and size limits, control characters, focus changes while waiting for modifier keys, and staged-secret scanning. No real microphone use, input, or permission changes occurred. Because the security changes restart the CLI between recording sessions, startup latency must be measured again during the next real-use validation.

## 2026-09-19 new-user workflow checks

- `pip install -e '.[dev]'` succeeded in a fresh virtual environment.
- Strict diagnostics for the Python.org framework, CLI, and dependencies passed.
- 99 automated tests and Ruff passed. Temporary directories and mocked system commands were used to validate installed-file placement, overwrite rejection, rollback on failure, data preservation during removal, installation locking, and service command flows.
- The generated app bundle passed ad-hoc signature checks, and the actual launcher passed `engine_bootstrap.py --check`. This check only loads modules; it does not start microphone, UI, or login operations.
- Regression coverage includes cancellation debounce, cancellation during startup, propagation of setup error codes, and handling shutdown requests before microphone access is approved.
- Full installation, automatic startup, and text insertion on a clean Mac, with user approval and real speech, remain unverified. The separate installation currently in use was not changed.

## 2026-09-19 browser focus regression checks

- 113 automated tests and Ruff passed. The Git-dependent test used the bundled Git
  executable because the system Git is blocked by the unaccepted Xcode license.
- New tests cover nested focus chains, focused text inside contenteditable, focused
  window fallback, deep/large web trees, writable custom editors, accessibility
  activation retry, process ownership, app switches, cycles and unfocused/read-only
  fields. All native frameworks are mocked; no microphone, real typing, permissions
  or login operations occur in these tests.
- The existing installed service was inspected read-only. Its logs show input-target
  capture failures in Chrome and Aside alongside successful captures in desktop apps.
  This identifies the failing stage, but does not prove which browser AX condition
  caused each failure. These initial checks did not include live browser dictation.

## 2026-09-19 approved input update and security checks

- The installed personal service was backed up and updated with the repository's
  input functions and input-validation helper after explicit authorization. Function
  ASTs and helper bytes were compared with the source. The service restarted into
  `idle`; microphone, real typing, permission and login operations were not automated.
- Native AXValue construction confirmed that PyObjC returns CFRange as a tuple;
  selection decoding and synthetic tests use the same representation.
- Cursor tests cover missing/malformed selection, a moved cursor in the same field,
  selected-text replacement with emoji using UTF-16 offsets, and stopping remaining
  chunks when cursor acknowledgement is lost.
- Privacy checks cover deleted historical runtime names sharing a blob with a safe
  filename, additional credentials, private file types and non-UTF-8 input.
- Repository/history and built wheel/sdist scans passed; pinned runtime dependency
  auditing found no known vulnerabilities. The final suite passed 124 tests and Ruff.
- After the installed input update, the user reported successful voice insertion in
  a Chrome input field using the existing shortcut. This is a user-reported manual
  success, not an automated observation. The site, field type, selection handling
  and failure cases were not independently verified. Aside and the rest of the full
  compatibility matrix still require manual validation.

## 2026-09-19 application-switch acknowledgement regression

- Follow-up use exposed truncation and false error HUDs after successful insertion.
  Diagnostics showed both immediate post-input rejection and the old 0.8-second
  acknowledgement timeout. The previous single successful Chrome test did not cover
  repeated app switches or asynchronous selection updates.
- The same synthetic incremental-delivery scenario fails against the previous
  insertion functions and passes against the corrected functions. It models a
  temporarily missing focus/selection, an intermediate caret, first-chunk delay over
  0.8 seconds, and multiple chunks with Korean/English/emoji. It verifies complete
  delivery, preserved adjacent text and no duplicate posts without real typing.
- Capture waits for matching cursor/content samples. Insertion pauses until a stable
  acknowledgement arrives (up to two seconds), and stops immediately on a confirmed
  app/field switch. Unavailable or intermediate AX data never authorizes another chunk.
- Exact full text is sufficient to acknowledge the final chunk even when the caret
  update is late. Intermediate chunks still require both text and cursor confirmation.
  Persistent failures keep the recovery transcript and stop without resending input.
- Payloads are limited by UTF-16 units, including surrogate-pair emoji. The HUD
  distinguishes incomplete insertion from failure before any input. Diagnostic logs
  contain fixed reason codes only, without text, selection offsets or credentials.
- 132 automated tests and Ruff passed. The installed input functions and HUD were
  updated with approval and the service returned to `idle`. Repeated live app-switch
  results are tracked separately from the synthetic regression evidence.
- The user initially reported successful complete insertion after app switches, then
  supplied another failure HUD. New diagnostics classified those later failures as
  `text_pending`, including after the final chunk. A read-only check found the full
  recovery text present in an Aside input, but the original expected baseline was
  no longer available. This residual false-negative remains under investigation;
  it is not evidence that all repeated-switch cases are fixed. Mismatch diagnostics
  now distinguish nonbreaking spaces, terminal newlines, outer whitespace, partial
  prefixes and other differences using fixed codes only.

## 2026-09-19 completion and truncated-input follow-up

- Read-only diagnostics from the installed personal service show persistent
  `text_pending_prefix` failures both between chunks and after the last chunk,
  and a final `text_pending_trailing_newline` failure. The latter means the strings
  differ only in terminal newlines. Prefix codes alone cannot distinguish an AX
  snapshot lag from actual character loss. These records do not establish that an
  app switch caused each incident, or that audio capture lost the utterance tail.
- The previous implementation sends at most 16 UTF-16 units, then stops sending
  the remaining sentence when the expected field value is not acknowledged within
  two seconds. Its exact comparison rejects an app-added paragraph newline, and
  its final foreground check rejects a completed write after switching apps.
- Synthetic regressions reproduce those failures against the previous source.
  The updated source prefers a single `AXSelectedText` replacement when supported,
  verifies text on the originally captured element after the final write, and
  handles an added terminal newline without accepting partial words or missing
  baseline text. Full-range text reads avoid relying solely on a rendered AXValue.
- Native writes, keyboard fallback, delayed acknowledgement, surrogate-pair
  selection replacement, app switches before/mid/after insertion, unexpected text,
  cancellation, modifiers, torn character counts and lost cursor acknowledgement
  are covered with mocked native frameworks. An attempted native write never falls
  back or retries, including when the API reports an error after accepting text.
- Unconfirmed final input retains the recovery file and does not produce a success
  sound or a definitive failure claim. The HUD has a separate six-second
  confirmation-needed state. Real inter-chunk focus changes still stop later input.
- 153 unit tests passed, Ruff passed, and `git diff --check` passed. Tests used the
  Command Line Tools Git because the default Git requires Xcode license acceptance.
  No license, permissions, login, microphone, or real typing changes were made.
- A read-only query confirmed that the inspected Aside editor exposes writable
  selected text and full-range reads. This checks capability only; the new native
  write path has not been exercised with real input. Codex and other app/editor
  combinations remain manual acceptance targets.
- The initial repair changed only repository files. After explicit subsequent
  authorization to update the installed service, the previous service and HUD
  files were backed up privately. Twelve input/state definitions were copied from
  the repository with AST equality checks; the HUD and IPC helper were checked
  byte-for-byte. Existing provider code, account state and runtime locations were
  retained. The state worker and matching trigger were updated together so that
  cancellation remains recoverable on the next recording.
- 54 relevant policy tests also passed against the staged personal-service files,
  with all native input and permission frameworks mocked. The installed files
  matched that tested stage. A graceful service stop and restart completed, a new
  process was observed, and the fresh service status reached `idle`.
- Restart readiness is not real dictation acceptance. Repeated live speech and
  app-switch validation of the new insertion path remain manual acceptance gates;
  microphone use, real typing, permission prompts and login changes were not
  automated during deployment.

## 2026-09-19 requested live acceptance checks

Environment: macOS 27.0, Chrome 153.0.8010.52, Python 3.13.3. These were separately
requested interactive checks in disposable localhost fields, not additions to the
automated test suite. No messages or forms were submitted.

- Live Chrome exposed a flaw in the proposed native write path: `AXSelectedText`
  was reported writable and its setter returned zero, but the selected text and
  DOM value remained unchanged. Capability checks and mock success had not proved
  actual insertion. This path was removed from the repository and installed
  service. Unicode keyboard input is the sole insertion path again; AX is used
  for focus and acknowledgement, not trial text writes.
- Actual keyboard delivery of a Korean/English/emoji sentence was confirmed in
  a single-line input, textarea and contenteditable. Independent DOM checks and
  captured-element reads verified full text and preservation of neighboring text.
  The clipboard change count was unchanged. These checks isolate insertion;
  they are not speech-recognition tests.
- With the Cocoa main run loop running, as in the real service, changing apps
  before insertion produced `app_changed`, zero keyboard events and unchanged
  baseline text. Switching apps after the last write still confirmed the full
  text in the original field. An earlier harness without a running main loop
  cached the foreground application and is excluded from app-switch evidence.
- Concurrent manual use caused early attempts to stop at their target guards.
  Those attempts are not counted as passing insertion checks. Existing user text
  in the test pages was preserved rather than reset for another attempt.
- One physical-microphone attempt used the installed service's recording toggle,
  macOS speech playback through the existing speaker route, the existing RODE
  input route, AGY transcription and automatic insertion. The observed states were
  `connecting`, `recording`, `transcribing`, `inserting`, `idle`; completion took
  about 1.7 seconds after stopping. Clipboard and foreground remained unchanged.
  **The transcript did not match the spoken test sentence or its final marker.**
  This confirms that the pipeline ran, not recognition accuracy or absence of
  utterance-tail loss. A natural-voice check was requested from the user and remains
  pending. The real physical hotkey was not certified by this scripted toggle.
- No audio, transcript contents or account information were written to test logs.
  Existing recovery text was preserved. Audio routing, permissions and login were
  not changed, and the test recording ended.
- After removing native trial writes, all 153 unit tests, 54 relevant tests against
  the installed source, and Ruff passed. The installed input definitions match the
  repository, and its restart reached `idle`. Broader app/IME and natural-voice
  acceptance remain unverified; the earlier native-write design is superseded by
  this correction.

## 2026-09-19 failed natural-voice report and HUD correction

- The user supplied a new screenshot after speaking the requested test sentence.
  Only the first 16 UTF-16 units were visible, and the HUD reported incomplete
  insertion. Contemporaneous fixed-code logs recorded `text_pending_prefix` with
  `final=False`. This disproves acceptance of the previous repair. The screenshot
  also shows a white rectangular backing outside the HUD's rounded corners.
- The old chunk gate can reject an already-delivered chunk solely because AX text
  readback is stale, without considering a correctly advanced caret. A regression
  models the reported first-chunk cutoff with stale text and stable caret progress:
  it fails with the previous version's same timeout and passes with the new code.
  The original incident logs did not include caret evidence, so this model is not
  presented as a reconstruction of every native AX snapshot from that incident.
- Input now has separate full-text and cursor acknowledgements. Cursor-only
  acknowledgement requires the same focused field, a stable exact expected caret,
  and readback equal to the captured baseline or an earlier stage of our own input.
  Existing prefix/suffix context must be preserved. First-write validation,
  unexpected text, cursor changes, cancellation and actual app/field switches
  remain guarded. All chunks are sent without retrying when those conditions hold.
- If only cursor acknowledgement is available at the end, the HUD reports input
  sent and keeps recovery text rather than reporting incomplete insertion or
  claiming a verified full-text match. Timeout diagnostics now add only boolean
  caret/snapshot indicators; no text or offsets are logged.
- The visual-effect background was replaced by an explicitly painted rounded
  transparent view. Native AppKit offscreen rendering measured zero alpha at all
  four corners, with a nonzero dark center. The panel stayed hidden/non-key and
  the foreground application was unchanged during this rendering check. This is
  rendering evidence, not a new live microphone or window-compositor test.
- All 161 unit tests, 62 relevant policy tests against staged installed files and
  Ruff passed. The installed service/HUD were privately backed up and updated,
  their bytes matched the tested stage, and a new process reached `idle`.
- No microphone, real typing, new test input windows or permission changes were
  used in this follow-up. Natural-voice acceptance in the user's actual editor is
  still required before claiming the truncation issue fully resolved.
