# Publication and release checklist

The project is MIT licensed and publicly available at [kyleryu98/agy-dictation](https://github.com/kyleryu98/agy-dictation). The items below track publication and release checks. Unchecked items are not complete.

## Code and documentation

- [x] Python requirements and development dependencies in `pyproject.toml` match the README commands.
- [x] Documentation matches module responsibilities, configuration, hotkeys, and the actual service entry point.
- [x] Verify the number of tests run and their results; do not treat discovery of zero tests as success.
- [x] Diagnostics do not overstate validation of actual voice input or account eligibility.
- [x] The build script only stages the app and LaunchAgent metadata; it does not install, register, or run them.

## Accounts and data

- [x] Distinguish guidance on CLI access for free personal accounts from actual free-account voice testing. Do not claim verified free voice use or unlimited usage when untested.
- [x] State that voice is unsupported for business and enterprise accounts and that the exact Gemini Audio model version is unconfirmed.
- [x] Users handle login, consent, and working-directory trust themselves in the official CLI.
- [x] Check the current publication-candidate source, documentation, wheel/sdist, and Git history for authentication data, tokens, real transcripts, audio, logs, and personally identifying paths.
- [x] Set public author information and the work email domain with user approval. Recheck history with `--check-identity` after committing.
- [x] Regression-test temporary-data access restrictions, link rejection, single-use callbacks, and cancellation cleanup.

## macOS validation

- [ ] Install the rebuilt version on a clean Mac and perform real end-to-end validation using the [test procedure](testing.md).
- [ ] Verify actual voice insertion, focus and clipboard preservation, cancellation during recording and processing, and preservation of existing text.
- [ ] Verify late results after cancellation, rapid restarts, focus changes, denied permissions, and network failures.
- [ ] Record results for the original personal implementation separately from those for the rebuilt version.

## Distribution and licensing

- [x] The owner selected the MIT license, reflected in `LICENSE`, package metadata, and third-party software notices.
- [x] Create the public repository and add its exact URL to the documentation: [https://github.com/kyleryu98/agy-dictation](https://github.com/kyleryu98/agy-dictation).
- [x] Document the local bundle's dependency on the existing Python framework and modules, and the requirement to retain the source directory.
- [ ] If targeting standalone distribution, separately complete dependency packaging, signing, notarization, and execution checks on a clean Mac.
- [x] Provide installation, start, stop, and removal tools with usage instructions, and test their file and command flows in isolation.
- [ ] On a clean Mac, verify actual service registration, logging back in, permission approval, and voice input.
- [x] Mark Windows as unsupported until it has been ported and validated.
