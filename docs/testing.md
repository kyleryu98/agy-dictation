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
