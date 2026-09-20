# Privacy and authentication boundaries

The user's official AGY CLI handles login and credentials. Do not include account emails, authentication files, tokens, real transcripts, audio files, or runtime logs in this project's documentation or repository. Issue reports should use synthetic examples instead of actual speech, along with environment details stripped of sensitive information.

## Data flow

Audio is processed through the official CLI's voice feature. The transcript passes through an external-editor callback and the wrapper to the target app. This project does not claim on-device transcription or offline-only processing. The official service's transmission, retention, and training policies are governed by the Google policies applicable to the user's account. This wrapper does not change those policies or guarantee data deletion.

The voice helper captures the selected microphone only for a requested recording and
sends in-memory PCM to the CLI through its `ANTIGRAVITY_MIC` loopback route. The listener
binds only to `127.0.0.1` on an ephemeral port and rejects connections while disarmed.
This audio transport does not authenticate TCP peers and must never be exposed through
a tunnel or public bind address. Local processes are outside its isolation boundary.
No raw audio is written to disk. RMS/peak measurements and device metadata travel over
the existing private control IPC; the meters use the same audio supplied to the CLI.
The microphone is stopped before provider finalization, on cancellation and on shutdown.

Avoiding the clipboard removes one path that could leave a transcript there. It does not mean that no traces remain in memory, temporary files, internal CLI records, or the target app's documents and autosaves. Check the implementation and actual behavior for callback temporary-data locations, deletion timing, and leftovers after errors or forced termination. The rebuilt repository's file and response retention boundaries have undergone unit tests and code review. The CLI's own retention policies and clean-Mac end-to-end validation are separate matters. See the [security audit](security-audit.md).

## Local retention in the current source

The default data directory is `~/Library/Application Support/ProListenDictation`; it can be changed with `AGY_DICTATION_DATA_DIR`. The external-editor callback delivers the transcript in `transcript.json`. Before insertion, the service writes `last-transcript.txt` and deletes it when the insertion result is confirmed in the target app. If insertion fails or cannot be verified, the transcript may remain in the recovery file; the user should delete it when it is no longer needed. The implementation checks file permissions of 0600, directory permissions of 0700, ownership, types, and links. It accepts only results with matching recording-session and single-use request identifiers, and cleans up exchange files on cancellation or error. It does not guarantee removal of all residual data after forced termination or power loss.

`settings.json` contains the shortcut preset, sound preference and optionally a device
UID. Device UIDs can identify attached hardware and must stay private. Login-startup
state remains in launchd. Raw audio and meter history are not retained.

Global key-down and mouse-press callbacks maintain only an in-memory activity
counter, used to stop dictation if the user interacts during processing or input.
Key contents, click coordinates and input history are not stored. Dictation's own
tagged Unicode events do not increment this counter. A late text readback may leave
the recovery file in place even after the completion HUD appears.

These files are local runtime data and must not be included in the repository, issues, or distribution artifacts. The default log location is `~/Library/Logs/ProListenDictation`. Do not publish logs or attach their raw contents. The wrapper is designed to omit transcripts and raw exception messages from logs; status codes and exception types remain. The official CLI's own logs are separate and should not be published in raw form either.

## User consent and permissions

Run `agy` yourself in a terminal to complete login and initial setup. Decide personally whether to approve any additional consent or working-directory trust requested by the CLI. Before granting macOS microphone access or permissions needed for global input and native insertion, verify which executable is requesting them. Do not describe this tool as providing automatic approval, permission bypasses, or automatic trust configuration.

## Checks before publication

- Check documentation, source examples, and packaging metadata for personal home paths and account identifiers.
- Check distribution artifacts and Git history for authentication data, tokens, transcripts, audio, and logs.
- Verify callback temporary-file permissions and cleanup after normal completion, cancellation, and errors.
- Verify that delayed results after cancellation are not inserted into another app or a new input position.

This document describes design boundaries and validation requirements. It is not an independent privacy certification.
