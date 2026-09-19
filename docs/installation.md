# Installing from source on macOS

This is a **developer preview** that uses the Python.org framework and a local Python environment. It is not a standalone distribution. Follow the README in order: prepare the environment → build → install → set up the CLI → configure permissions → start.

## What each command does

| Command | Scope of changes |
| --- | --- |
| `doctor.py` | Reads installation prerequisites only. `--strict` fails if prerequisites are missing; `--installed` also checks installed files |
| `build_macos.py --output dist` | Creates a development app, registration metadata, and an editor executable in the specified empty output directory |
| `manage_macos.py install --from-build dist --apply` | Installs into the user's Applications folder and this tool's dedicated data, log, and LaunchAgent locations. Does not start the service |
| `manage_macos.py setup-cli --apply` | Runs the user's official AGY CLI interactively in the transcription working directory |
| `manage_macos.py permissions` | Shows the app locations for permission approval. Does not change permissions |
| `manage_macos.py start --apply` | Registers or restarts the user's login service |
| `manage_macos.py stop --apply` | Stops this version's service and authenticated engine |
| `manage_macos.py status` | Checks registration and the presence of installed files. Does not prove successful recording |
| `manage_macos.py uninstall --apply` | Removes this version's installed files. Preserves user data, logs, and the official CLI account |

Omitting `--apply` from a command that makes changes prints the plan only. Automated tests use temporary directories and mocked system commands; they do not act on the current installation.

## Default locations

- App: `~/Applications/ProListen Voice Engine.app`
- Data: `~/Library/Application Support/ProListenDictation`
- Logs: `~/Library/Logs/ProListenDictation`
- Login service: `~/Library/LaunchAgents/com.prolisten.agy-dictation.plist`

The installation record is stored as `installation.json` in the data directory. Removal is refused if the record does not match the target. If you moved a directory or built in another environment, restore the original location and Python environment before proceeding.

## Python environment

Use the same `.venv/bin/python` for the build, installation, and startup tools. Installation is refused if its Python interpreter differs from the one used to build. Keep `.venv`, the repository path, and the existing Python framework in place after installation. The bundle is ad-hoc signed; it is not a standalone product signed with Developer ID and notarized.

## Login and permissions

`setup-cli` runs AGY in the same working directory used for transcription sessions. Logging in from another terminal directory does not mean that you have completed the trust prompt for this directory. Decide for yourself whether to accept terms, data sharing, and folder trust.

First grant Python permission to control input, then start the service and approve the voice engine's microphone request. System Settings labels vary by macOS version. After changing permissions, restart with `start --apply`.

## Updating

1. Run `stop --apply` and `uninstall --apply` using the existing version.
2. Update the source and refresh dependencies in the same virtual environment.
3. Build into a new, empty output directory instead of reusing the existing `dist`.
4. Install from the new build and start the service.

The new version refuses to start if the existing personal `com.antigravity.dictation` service is running. It does not automatically stop or overwrite other services.

## Data left after removal

Recovery transcripts and logs are intentionally retained. When you no longer need them, inspect the dedicated data and log directories before removing their contents. Uninstalling this tool does not remove official AGY CLI login information or automatically change entries in the macOS permission lists.
