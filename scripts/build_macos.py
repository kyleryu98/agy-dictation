#!/usr/bin/env python3
"""Stage a machine-local development app. Never install, launch, or grant permissions."""

import argparse
import json
import plistlib
import shlex
import shutil
import site
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agy_dictation.config import (  # noqa: E402
    BASE, LOG, ENGINE_APP, ENGINE_BUNDLE_ID, SERVICE_LABEL, AGY, DISPLAY_NAME,
)


def runtime_settings():
    return {
        "AGY_DICTATION_DATA_DIR": str(BASE.absolute()),
        "AGY_DICTATION_LOG_DIR": str(LOG.absolute()),
        "AGY_DICTATION_ENGINE_APP": str(ENGINE_APP.absolute()),
        "AGY_DICTATION_CLI": str(AGY.absolute()),
    }


def launch_agent(python: Path, source: Path) -> dict:
    return {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [str(python), "-m", "agy_dictation.macos.service"],
        "WorkingDirectory": str(BASE),
        "EnvironmentVariables": {
            "PYTHONPATH": str(source),
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            **runtime_settings(),
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 15,
        "ProcessType": "Interactive",
        "StandardOutPath": str(LOG / "stdout.log"),
        "StandardErrorPath": str(LOG / "stderr.log"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--no-sign", action="store_true", help="Stage only; resulting app is not signed."
    )
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("This local development bundle builder requires macOS.")
    launcher = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
    if not launcher.is_file():
        parser.error("A Python.org framework build is required. No system Python is modified.")
    destination = args.output.expanduser().resolve()
    protected = [
        ROOT / name for name in ("src", "tests", "scripts", "docs", "packaging", ".git", ".github")
    ]
    if (
        destination == ROOT
        or ROOT.is_relative_to(destination)
        or any(destination.is_relative_to(path) for path in protected)
    ):
        parser.error("Output must not overlap source, tests, documentation, or Git metadata.")
    if any(path.is_symlink() for path in (ROOT / "src/agy_dictation").rglob("*")):
        parser.error("Source symlinks are not accepted for application builds.")
    if destination.exists() and any(destination.iterdir()):
        parser.error("Output directory must be empty; use a different --output path.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Even a custom output folder inside the repository must never be committed.
    (destination / ".gitignore").write_text("*\n")
    (destination / "DO_NOT_PUBLISH.txt").write_text(
        "Machine-local development build: contains absolute local paths. Do not upload.\n"
    )
    app = destination / ENGINE_APP.name
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    resources = contents / "Resources"
    (resources / "python").mkdir(parents=True)
    shutil.copy2(launcher, contents / "MacOS/AGYVoiceEngine")
    shutil.copytree(
        ROOT / "src/agy_dictation",
        resources / "python/agy_dictation",
        ignore=lambda folder, names: [
            name
            for name in names
            if name == "__pycache__" or (Path(folder, name).is_file() and not name.endswith(".py"))
        ],
    )
    info = {
        "CFBundleIdentifier": ENGINE_BUNDLE_ID,
        "CFBundleExecutable": "AGYVoiceEngine",
        "CFBundleName": f"{DISPLAY_NAME} Voice Engine",
        "CFBundleDisplayName": f"{DISPLAY_NAME} Voice Engine",
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": "단축키로 시작한 음성을 본인의 AGY 계정으로 전사합니다.",
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    paths = site.getsitepackages()
    if site.ENABLE_USER_SITE:
        paths.append(site.getusersitepackages())
    (resources / "runtime.json").write_text(
        json.dumps({"dependency_paths": paths, "settings": runtime_settings()}, indent=2)
    )
    (resources / "engine_bootstrap.py").write_text("""import importlib, json, os, runpy, sys
from pathlib import Path
sys.dont_write_bytecode = True
resources = Path(__file__).resolve().parent
config = json.loads((resources / "runtime.json").read_text())
os.environ.update(config["settings"])
for path in config["dependency_paths"]:
    if path not in sys.path: sys.path.append(path)
sys.path.insert(0, str(resources / "python"))
if "--check" in sys.argv:
    importlib.import_module("agy_dictation.macos.engine")
    print("Packaged engine imports OK; no microphone or UI started.")
else:
    runpy.run_module("agy_dictation.macos.engine", run_name="__main__")
""")
    bridge = destination / "support/bin/prolisten-agy-export-prompt"
    bridge.parent.mkdir(parents=True)
    bridge.write_text(
        "#!/bin/sh\nexport PYTHONPATH="
        + shlex.quote(str(ROOT / "src"))
        + "\nexec "
        + shlex.join([sys.executable, "-m", "agy_dictation.export_prompt"])
        + ' "$@"\n'
    )
    bridge.chmod(0o700)
    (destination / f"{SERVICE_LABEL}.plist").write_bytes(
        plistlib.dumps(launch_agent(Path(sys.executable), ROOT / "src"))
    )
    if not args.no_sign:
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--options",
                "runtime",
                "--entitlements",
                str(ROOT / "packaging/macos/entitlements.plist"),
                str(app),
            ],
            check=True,
        )
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True)
    (
        destination / "INSTALL.txt"
    ).write_text(f"""LOCAL DEVELOPMENT BUILD ONLY — not a standalone distribution.
Nothing was installed or started. No credentials are included.

Installation is an explicit separate command:
{sys.executable} scripts/manage_macos.py install --from-build {destination} --apply
Then run setup-cli --apply to finish your own CLI login, followed by start --apply.
Stop or uninstall using the same manage_macos.py tool. No action is automatic.

Before installation:
- Keep this checkout and its Python environment at their current locations.
- Ensure official agy is installed and sign in interactively using your own account.
- Avoid running another dictation service with the same global hotkey.

Installation locations (review before copying):
App: {ENGINE_APP}
Editor bridge: {BASE / "bin/prolisten-agy-export-prompt"}
LaunchAgent: {Path.home() / "Library/LaunchAgents" / (SERVICE_LABEL + ".plist")}
Create private directories: {BASE}, {LOG}, {BASE / "voice-session"}
Perform AGY CLI first-run login and folder trust interactively in the voice-session directory.
Register the LaunchAgent explicitly only after copying files and granting the requested permissions.
This build requires the local Python framework and dependencies; do not distribute it as a self-contained installer.
""")
    print(f"Staged: {destination}\nNo installation, service registration, or launch performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
