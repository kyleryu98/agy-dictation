# Pre-publication privacy and security audit

Audit date: 2026-09-19. Scope: this repository's publication-candidate source, documentation, and configuration; Git objects and history; and generated wheel/sdist packages. The separate personal installation in active use was not changed.

## Findings

- No personal home paths, account emails, authentication tokens, private keys, real transcripts, recordings, or logs were found in the publication candidate.
- At the initial inspection, there were no commits, Git objects, or remote repository. Commit history must also be rechecked after the first commit. The repository is now public at [kyleryu98/agy-dictation](https://github.com/kyleryu98/agy-dictation) under the MIT license; publication does not expand the scope of the initial audit.
- Whether to publish an author email was undecided during the initial inspection. The user later chose to publish a work email, so `privacy-policy.json` allows the `prolisten.net` domain for commit authors and committers. The name and work email are public commit metadata.
- A local pre-commit guard was installed. It allows the approved work email domain and GitHub no-reply addresses, and blocks other addresses. No GitHub upload or push was performed as part of that initial audit; the repository has since been published.

## Checks performed

| Check | Result and scope |
| --- | --- |
| Local privacy scan | Publication-candidate files, staged snapshot, and reachable Git history and commit metadata |
| detect-secrets | Offline pattern scan: zero candidates. Token validity was not checked online |
| Python package inspection | Inspected wheel/sdist contents without extracting them: zero personal-data or secret candidates |
| pip-audit | Zero known vulnerabilities reported across the 12 installed runtime and transitive dependencies at the time of inspection |
| Bandit | Zero high- or medium-severity findings. The 13 low-severity findings were reviewed as necessary subprocess calls and error-cleanup paths |
| Automated tests | 65 passed. Regression tests without real microphone use, user input, or permission changes |
| Ruff and package build | Passed |
| Existing installation preservation | Active installation files outside the repository were not changed |

These results apply to the scope and time of the inspection. They do not guarantee detection of unknown vulnerabilities or every form of personal data. Rerun the checks when adding code, dependencies, or files.

## Risks addressed

- Runtime files: directory permissions of 0700 and file permissions of 0600; ownership, file-type, and link-count checks; rejection of symbolic links; unique temporary files and atomic replacement.
- Transcript delivery: single-use requests tied to recording sessions; rejection of stale or duplicate responses; exchange-file cleanup after cancellation or errors. Restarting the CLI between sessions prevents an earlier editor callback from consuming a new request.
- Local IPC: same-user verification, a random per-engine capability, command and response schemas, frame-size and time limits, concurrency limits, and a lock against duplicate engine instances.
- Input: rejection of control characters, invalid Unicode, and excessive length; focus rechecking after waiting for modifier keys to be released. Removed a recording-start signal used for debugging.
- Logs: fixed codes and exception types instead of raw received content, exception messages, or transcripts in logs and error responses. Removed target-app identifier logging.
- Subprocesses: restrictive umask, removal of environment variables that permit loader or interpreter injection, absolute editor paths, and child-process-group cleanup.
- CI: official GitHub Actions pinned to verified commit SHAs, with credential persistence disabled.
- Builds: automatic Git exclusions for output directories; rejection of output paths that overlap the source directory and rejection of source symbolic links.

## Local artifacts that must not be published

Development `.app` bundles, `runtime.json`, LaunchAgent plists, runtime editor wrappers, and `INSTALL.txt` **may contain absolute paths from the build machine.** They are not standalone distribution artifacts. Keep them separate from source and out of Git. The build script also creates exclusion rules for custom output directories. Do not archive and publish the `.git` directory or local hooks.

## Commands to run before publication

```sh
python scripts/prepublish_check.py
python scripts/prepublish_check.py --check-identity
python scripts/prepublish_check.py --artifact dist/package.whl --artifact dist/package.tar.gz
python scripts/install_git_guard.py --apply
```

If system Git is unavailable, set `AGY_AUDIT_GIT` to the path of a working Git executable. Scan output shows only the file, line, and issue type, not the matching secret value. Use `--personal-marker` to check locally for additional identifiers that must not be published. Do not record those values in the repository.

Set the approved work email domain or your own GitHub no-reply address in **this repository's local Git configuration**, then run the author-identity check. Work-domain approval is recorded in the tracked `privacy-policy.json`; it does not relax secret scanning of source or packages. Hooks are bypassable development tools and do not replace a manual check immediately before publication.

## Remaining validation and boundaries

- Installation of the security-hardened version on a clean Mac, real voice input, startup latency, and per-app compatibility have not been revalidated. The MIT license is in place; distribution signing, notarization, and Windows implementation are separate steps.
- The design assumes a trusted user account and configured parent directories. It does not isolate the app from malware with the same user's privileges or from an administrator/root user.
- Final input events and simultaneous user actions cannot be made fully atomic. Some input fields or apps may fail conservatively.
- Transcription uses the official AGY CLI's cloud feature. This repository does not control Google's data processing or retention of the CLI's own logs and temporary files.
