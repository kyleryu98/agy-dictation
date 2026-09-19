# AGY Dictation

**English** | [한국어](README.ko.md)

Press a shortcut, speak, and insert the transcript at your cursor—with a recording and processing overlay. AGY Dictation is an independent macOS tool that uses voice transcription in the official Antigravity CLI. It is not an official Google product.

- Press **Control + backtick** or **Control + backslash** to start recording; press again to transcribe and insert.
- See recording time and processing status in a small overlay at the bottom of the screen.
- Cancel with **Esc** or the overlay's × button.
- Keep focus in your app, without switching windows or using the clipboard.

> **macOS developer preview.** Installation is from source. There is no standalone DMG, Windows support, or guarantee of compatibility with every app and input field. The current overlay text is in Korean; English documentation does not change the app's UI language.

## Requirements

1. **macOS and a Python.org framework installation of Python.** The original environment was tested with Python 3.13. Use the [Python macOS installer](https://www.python.org/downloads/macos/). A working `python3` command alone does not guarantee that the helper app can be built; some Homebrew installations may not meet its requirements.
2. Install the [official AGY CLI](https://antigravity.google/docs/cli/install) and confirm that `agy --version` works.
3. Use your own **personal AGY account**. Official documentation describes CLI access for free personal accounts, but this project has not tested voice transcription on a free account and does not promise unlimited usage. The referenced voice documentation lists business and enterprise accounts as unsupported.
4. Clone the repository and run the following commands from its root:

```sh
git clone https://github.com/kyleryu98/agy-dictation.git
cd agy-dictation
```

## Install and start

### 1. Prepare the environment and build the helper

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/doctor.py --strict
.venv/bin/python scripts/build_macos.py --output dist
```

Resolve any missing requirements reported by `doctor --strict` before continuing. The build writes to `dist`; it does not install or start anything. If that directory already exists, use a different, empty output directory.

### 2. Install on this Mac and set up the CLI

```sh
.venv/bin/python scripts/manage_macos.py install --from-build dist --apply
.venv/bin/python scripts/manage_macos.py setup-cli --apply
```

`setup-cli` opens the official CLI in the dictation working directory. Complete login, onboarding, terms, and directory trust yourself, then exit the CLI. The integration does not accept terms for you or automatically submit agent tasks.

Installation refuses to overwrite existing app or service files. It does not start the login service at this stage.

### 3. Grant macOS permissions and start the service

```sh
.venv/bin/python scripts/manage_macos.py permissions
```

Allow the Python app shown in the output under **System Settings → Privacy & Security → Accessibility**. Depending on your macOS version, the permission category may be labeled **Device Control & Data Access**.

```sh
.venv/bin/python scripts/manage_macos.py start --apply
.venv/bin/python scripts/manage_macos.py status
```

Approve **ProListen Voice Engine's microphone request** when it appears. If the service stopped before you granted permission, run `start --apply` again. Permissions are never granted automatically.

### 4. Try dictation

Open a blank TextEdit document and click where you want to type. Press **Control + backtick** or **Control + backslash**, wait for the recording overlay, and speak. Press the same shortcut again to finish. The overlay should progress from recording to processing to completion. The tool does not press Enter or send your message.

On Korean keyboards, the shortcut is commonly labeled **Control + ₩**. The listener recognizes the physical grave/backtick and backslash keys; the location of the ₩ label varies by keyboard. The current overlay uses Korean text: `녹음 중` (recording), `글로 바꾸는 중` (transcribing), and `입력 완료` (inserted).

## Automatic startup

`start --apply` registers a per-user login service. It is configured to start at subsequent logins without an AGY desktop app or terminal window. Internet access and a valid AGY login are still required. Startup after a fresh login on another Mac remains part of the validation checklist.

**Do not move or delete the repository, `.venv`, or Python framework after installation.** The development helper depends on their local paths. Other users should build from source on their own Mac instead of copying your `dist` folder.

## Stop, update, or uninstall

```sh
.venv/bin/python scripts/manage_macos.py stop --apply
.venv/bin/python scripts/manage_macos.py uninstall --apply
```

Stopping shuts down this version's service and authenticated voice engine. Uninstalling removes its app, launcher, and LaunchAgent. It **preserves logs, recovery transcripts, official AGY login data, and the source checkout**.

To update: stop → uninstall → update source and dependencies → build into a new output directory → install → start. See the [installation guide](docs/installation.md).

Without `--apply`, lifecycle commands only show a plan. `status`, `permissions`, and `doctor.py` are read-only. Avoid assigning the same shortcut to multiple dictation tools.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Helper build fails | Run `doctor.py --strict`; check the Python.org framework installation. |
| Shortcut does nothing | Check Python's control permission, field focus, service status, and shortcut conflicts. |
| Login or directory trust is required | Run `setup-cli --apply` and complete setup interactively. |
| Microphone permission is required | Allow microphone access for ProListen Voice Engine, then restart. |
| Input target changed | Keep the same field focused and avoid editing its contents while recording. |
| An existing installation is detected | Stop and uninstall that version first; do not overwrite files manually. |

After an error, a recovery transcript may remain at `~/Library/Application Support/ProListenDictation/last-transcript.txt`. Do not post raw transcripts or logs in public issues.

## Validation and limitations

- The original personal implementation was tested with Korean speech inserted directly into TextEdit, unchanged focus and clipboard, and cancellation.
- The reorganized source has static checks, unit tests, package builds, and isolated installation/removal tests. **Real account login, permission setup, and microphone input on a clean Mac have not been validated.** See the [testing record](docs/testing.md).
- The integration depends on the CLI's interactive voice and external-editor features. CLI updates may affect it. The exact transcription model version is unconfirmed.
- Password fields and apps that do not expose sufficient accessibility information may be unsupported. Reboot behavior, different input methods, and app compatibility still need real-world testing.

## Development and documentation

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m ruff check .
```

Automated tests must not use a real microphone, type into user applications, change login state, or grant permissions.

```text
src/agy_dictation/     CLI integration, configuration, and result exchange
  macos/              Overlay, global shortcuts, and microphone helper
scripts/              Diagnostics, builds, and explicit lifecycle commands
packaging/macos/       App entitlement declarations
tests/                Isolated automated tests
docs/                 Architecture, installation, privacy, and validation
```

English is the primary documentation language. [README.ko.md](README.ko.md) provides the Korean installation and usage guide. Detailed technical and security documentation is maintained in English; keep both READMEs in sync when changing setup or behavior.

[Architecture](docs/architecture.md) · [Installation](docs/installation.md) · [Privacy](docs/privacy.md) · [Security audit](docs/security-audit.md) · [Windows port](docs/windows-port.md) · [Release checklist](docs/release-checklist.md)

Before publishing changes, run `python scripts/prepublish_check.py --check-identity` to check source, Git history, and author identity. Approved business email domains or GitHub no-reply addresses are allowed; token and personal-path checks remain enabled. Install the local commit guard with `python scripts/install_git_guard.py --apply`.

## License

The integration source is available under the [MIT License](LICENSE). The official AGY CLI, Python, and third-party libraries retain their own licenses and terms. See [third-party notices](THIRD_PARTY_NOTICES.md).

Official references: [Plans](https://antigravity.google/docs/plans) · [CLI installation](https://antigravity.google/docs/cli/install) · [Voice input](https://www.antigravity.google/docs/cli/commands/voice/)
