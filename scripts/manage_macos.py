#!/usr/bin/env python3
"""Explicit, user-scoped install/start/stop/uninstall. Defaults to a read-only plan."""

import argparse
from contextlib import contextmanager
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agy_dictation.config import (  # noqa: E402
    BASE,
    LOG,
    ENGINE_APP,
    ENGINE_BUNDLE_ID,
    EDITOR_BRIDGE,
    SERVICE_LABEL,
    AGY,
)
from agy_dictation import secure_files as sf  # noqa: E402


@dataclass(frozen=True)
class Layout:
    base: Path
    logs: Path
    app: Path
    agent: Path
    source: Path
    python: str
    label: str = SERVICE_LABEL

    @property
    def editor(self):
        return self.base / "bin" / EDITOR_BRIDGE.name


def local_layout():
    return Layout(
        BASE,
        LOG,
        ENGINE_APP,
        Path.home() / "Library/LaunchAgents" / f"{SERVICE_LABEL}.plist",
        ROOT,
        sys.executable,
    )


def no_links(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.rglob("*")):
        raise ValueError("Symlinked install artifacts are not accepted")


def validate_build(build, layout):
    app = build / layout.app.name
    agent = build / f"{layout.label}.plist"
    editor = build / "support/bin" / layout.editor.name
    if (
        not app.is_dir()
        or not agent.is_file()
        or not editor.is_file()
        or not (app / "Contents/MacOS/AGYVoiceEngine").is_file()
        or not (app / "Contents/Resources/engine_bootstrap.py").is_file()
    ):
        raise ValueError("Incomplete build; run build_macos.py first")
    no_links(build)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    manifest = plistlib.loads(agent.read_bytes())
    if info.get("CFBundleIdentifier") != ENGINE_BUNDLE_ID:
        raise ValueError("Unexpected helper application identity")
    if manifest.get("Label") != layout.label or manifest.get("ProgramArguments") != [
        layout.python,
        "-m",
        "agy_dictation.macos.service",
    ]:
        raise ValueError(
            "Build was produced with a different Python environment; rebuild with this interpreter"
        )
    env = manifest.get("EnvironmentVariables", {})
    if manifest.get("WorkingDirectory") != str(layout.base) or env.get("PYTHONPATH") != str(
        layout.source / "src"
    ):
        raise ValueError("Build locations differ from the active configuration; rebuild")
    return app, agent, editor


@contextmanager
def installation_lock(layout):
    import fcntl

    with sf.private_directory(layout.base) as directory:
        fd = sf.open_private_file(
            directory, "installation.lock", os.O_RDWR | os.O_CREAT, repair=True
        )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another installation operation is running") from None
        yield
    finally:
        os.close(fd)


def install(build, layout, run=subprocess.run):
    with installation_lock(layout):
        return _install_locked(build, layout, run)


def _install_locked(build, layout, run=subprocess.run):
    sources = validate_build(build, layout)
    for target in [layout.app, layout.agent, layout.editor]:
        if target.exists() or target.is_symlink():
            raise ValueError(
                "Install target already exists; stop and uninstall this version before upgrading"
            )
    run(["/usr/bin/codesign", "--verify", "--strict", str(sources[0])], check=True)
    for path in [layout.base, layout.logs, layout.base / "voice-session", layout.base / "bin"]:
        sf.ensure_private_dir(path)
    for name in ["stdout.log", "stderr.log"]:
        with sf.private_directory(layout.logs) as directory:
            os.close(
                sf.open_private_file(
                    directory, name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, repair=True
                )
            )
    installed = []
    try:
        layout.app.parent.mkdir(parents=True, exist_ok=True)
        layout.agent.parent.mkdir(parents=True, exist_ok=True)
        installed.append(layout.app)
        shutil.copytree(sources[0], layout.app)
        installed.append(layout.editor)
        shutil.copy2(sources[2], layout.editor)
        layout.editor.chmod(0o700)
        installed.append(layout.agent)
        shutil.copy2(sources[1], layout.agent)
        layout.agent.chmod(0o600)
        with sf.private_directory(layout.base) as directory:
            sf.atomic_write_json(
                directory,
                "installation.json",
                {
                    "schema": 1,
                    "label": layout.label,
                    "app": str(layout.app),
                    "agent": str(layout.agent),
                    "editor": str(layout.editor),
                    "source": str(layout.source),
                },
            )
    except Exception:
        for path in reversed(installed):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        raise


def validate_installation(layout):
    with sf.private_directory(layout.base) as directory:
        record = sf.read_json(directory, "installation.json", 8192)
    if any(
        record.get(k) != v
        for k, v in {
            "schema": 1,
            "label": layout.label,
            "app": str(layout.app),
            "agent": str(layout.agent),
            "editor": str(layout.editor),
            "source": str(layout.source),
        }.items()
    ):
        raise ValueError("Installation record does not match this checkout and paths")
    if layout.app.exists():
        no_links(layout.app)
        if (
            plistlib.loads((layout.app / "Contents/Info.plist").read_bytes()).get(
                "CFBundleIdentifier"
            )
            != ENGINE_BUNDLE_ID
        ):
            raise ValueError("Application identity changed; refusing removal")
    for file in [layout.agent, layout.editor]:
        if file.is_symlink():
            raise ValueError("Installation path is now a symlink")
    return record


def loaded(label, run=subprocess.run):
    result = run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True
    )
    return result.returncode == 0


def stop(layout, run=subprocess.run, engine_shutdown=None):
    if loaded(layout.label, run):
        run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{layout.label}"], check=True)
    deadline = time.monotonic() + 6
    while loaded(layout.label, run) and time.monotonic() < deadline:
        time.sleep(.1)
    if loaded(layout.label, run):
        raise ValueError("Service is still stopping; installation files were not changed")
    # Ask only our authenticated engine to stop. Never kill an arbitrary process.
    if engine_shutdown is None:
        from agy_dictation.macos.service import CLI

        engine_shutdown = CLI().shutdown
    engine_shutdown()
    deadline = time.monotonic() + 6
    while (layout.base / "engine.sock").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if (layout.base / "engine.sock").exists():
        if not remove_stale_socket(layout):
            raise ValueError("Engine is still running; application files were not removed")


def remove_stale_socket(layout):
    import fcntl

    with sf.private_directory(layout.base) as directory:
        try:
            fd = sf.open_private_file(directory, "engine.lock", os.O_RDWR)
        except FileNotFoundError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            try:
                info = os.stat("engine.sock", dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return True
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                return False
            sf.unlink(directory, "engine.sock")
            sf.unlink(directory, "engine-auth.json")
            return True
        finally:
            os.close(fd)


def start(layout, run=subprocess.run):
    validate_installation(layout)
    if loaded("com.antigravity.dictation", run):
        raise ValueError(
            "Legacy dictation service is active. Stop it explicitly before starting this version"
        )
    if loaded(layout.label, run):
        run(["/bin/launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{layout.label}"], check=True)
    else:
        run(["/bin/launchctl", "enable", f"gui/{os.getuid()}/{layout.label}"], check=True)
        run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(layout.agent)], check=True)


def uninstall(layout, stop_service=stop):
    with installation_lock(layout):
        return _uninstall_locked(layout, stop_service)


def _uninstall_locked(layout, stop_service):
    validate_installation(layout)
    stop_service(layout)
    if layout.app.exists():
        shutil.rmtree(layout.app)
    layout.agent.unlink(missing_ok=True)
    layout.editor.unlink(missing_ok=True)
    with sf.private_directory(layout.base) as directory:
        sf.unlink(directory, "installation.json")
    # Keep recordings' recovery text, logs, user authentication, and the checkout.


def runtime_state(layout):
    try:
        directory = os.open(layout.base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return "not_started"
    try:
        if os.fstat(directory).st_uid != os.geteuid():
            return "unavailable"
        data = sf.read_json(directory, "status.json", 16384)
        state = data.get("state")
        known = {
            "starting",
            "idle",
            "connecting",
            "recording",
            "transcribing",
            "inserting",
            "error",
            "permission_required",
        }
        return state if isinstance(state, str) and state in known else "unavailable"
    except (OSError, ValueError):
        return "unavailable"
    finally:
        os.close(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["install", "setup-cli", "start", "stop", "status", "permissions", "uninstall"],
    )
    parser.add_argument("--from-build", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Explicitly perform this action. Omit to inspect the plan.",
    )
    args = parser.parse_args(argv)
    layout = local_layout()
    if args.action == "permissions":
        print(
            json.dumps(
                {
                    "input_control_application": str(
                        Path(sys.base_prefix) / "Resources/Python.app"
                    ),
                    "microphone_application": str(layout.app),
                    "instructions": "Allow Python in Accessibility / Device Control, then start the service and allow the voice engine microphone prompt. No permission is granted automatically.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.action == "status":
        print(
            json.dumps(
                {
                    "installed": layout.agent.is_file() and layout.app.is_dir(),
                    "registered": loaded(layout.label) if sys.platform == "darwin" else False,
                    "last_runtime_state": runtime_state(layout),
                },
                indent=2,
            )
        )
        return 0
    if not args.apply:
        print(
            f"Plan only: {args.action}; no files, services, permissions, or accounts changed. Use --apply to proceed."
        )
        return 0
    if sys.platform != "darwin":
        parser.error("macOS is required")
    try:
        if args.action == "install":
            install(args.from_build.resolve(), layout)
        elif args.action == "setup-cli":
            if not sys.stdin.isatty():
                raise ValueError("Run setup-cli interactively in your own terminal")
            sf.ensure_private_dir(layout.base / "voice-session")
            subprocess.run([str(AGY)], cwd=layout.base / "voice-session", check=True)
        elif args.action == "start":
            start(layout)
        elif args.action == "stop":
            stop(layout)
        elif args.action == "uninstall":
            uninstall(layout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Action failed: {error}\n")
    print(
        f"{args.action} completed. Permission prompts and AGY account consent are handled by you."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
