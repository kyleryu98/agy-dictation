#!/usr/bin/env python3
"""Read-only diagnostics: no microphone, launchctl mutation, or sign-in."""

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agy_dictation.config import AGY, BASE, ENGINE_APP, EDITOR_BRIDGE  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true", help="Fail if build prerequisites are missing"
    )
    parser.add_argument("--installed", action="store_true", help="Also require installed files")
    args = parser.parse_args(argv)
    distributions = [
        "pyte",
        "pynput",
        "pyobjc-framework-Cocoa",
        "pyobjc-framework-Quartz",
        "pyobjc-framework-AVFoundation",
    ]
    dependencies = {}
    for name in distributions:
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    framework = (Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python").is_file()
    ready = (
        sys.platform == "darwin"
        and framework
        and all(dependencies.values())
        and AGY.is_file()
        and os.access(AGY, os.X_OK)
        and bool(shutil.which("codesign"))
    )
    installed = (
        ENGINE_APP.is_dir() and EDITOR_BRIDGE.is_file() and (BASE / "installation.json").is_file()
    )
    print(
        json.dumps(
            {
                "platform": platform.system(),
                "python": platform.python_version(),
                "python_framework_found": framework,
                "ready_to_build": ready,
                "installation_files_ready": installed,
                "macos_supported": sys.platform == "darwin",
                "agy_cli_found": AGY.is_file() and os.access(AGY, os.X_OK),
                "codesign_found": bool(shutil.which("codesign")),
                "dependencies": dependencies,
                "engine_app_installed": ENGINE_APP.exists(),
                "editor_bridge_installed": EDITOR_BRIDGE.is_file(),
                "data_directory_exists": BASE.exists(),
                "account_and_microphone": "Not inspected; test explicitly after local installation.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if (args.strict and not ready) or (args.installed and not installed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
