# Scope of a Windows port

**Windows is not implemented.** Windows support in the official AGY CLI is separate from Windows support in this wrapper. The current Python backend is Unix-only and depends on `pty` and `fcntl`; replacing the AppKit UI alone is not enough.

## Required work

| Area | Work to port and validate |
| --- | --- |
| CLI transport | Implement Windows interactive-console transport to replace Unix PTY and `fcntl`, and replace or verify support for local Unix-socket communication; validate input, output, termination, and cancellation with candidates such as ConPTY |
| External-editor callback | Windows path quoting, encoding, process invocation, temporary data, and session isolation |
| HUD | A non-activating status window and GUI event loop to replace AppKit |
| Global hotkeys | Control + grave/backslash and Esc handling; keyboard layouts, key repeat, and conflicts |
| Text insertion | Unicode insertion without the clipboard or app switching; focus, IME, selections, and permission boundaries |
| Automatic startup | Choose a LaunchAgent replacement; explicit installation and removal, and prevention of duplicate instances |
| Packaging | Python and dependency distribution, installation, updates, removal, and distribution signing |

This is an implementation plan, not evidence of success with any particular Windows API. Users must still handle login and consent themselves in the official CLI on Windows.

## Completion criteria

Validate real speech-to-text insertion into a normal text editor on a clean Windows environment. Test clipboard and focus preservation, cancellation during recording and processing, rejection of delayed callbacks, Korean and English text, emoji, network failures, and process termination. Separately document limitations for target apps running with standard or different privileges and for secure input fields. Existing macOS successes are not evidence of Windows support.
