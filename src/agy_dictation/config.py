"""Runtime locations. Importing this module never creates files or starts services."""

import os
import shutil
import sys
from pathlib import Path

from .secure_files import ensure_private_dir as ensure_private_dir

APP_NAME = "ProListenDictation"
SERVICE_LABEL = "com.prolisten.agy-dictation"
ENGINE_BUNDLE_ID = "com.prolisten.agy-voice-engine"
if sys.platform == "darwin":
    default_base = Path.home() / "Library/Application Support" / APP_NAME
    default_log = Path.home() / "Library/Logs" / APP_NAME
elif sys.platform == "win32":
    default_base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP_NAME
    default_log = default_base / "logs"
else:
    default_base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_NAME
    default_log = default_base / "logs"

BASE = Path(os.environ.get("AGY_DICTATION_DATA_DIR", default_base)).expanduser()
LOG = Path(os.environ.get("AGY_DICTATION_LOG_DIR", default_log)).expanduser()
AGY = Path(
    os.environ.get("AGY_DICTATION_CLI", shutil.which("agy") or str(Path.home() / ".local/bin/agy"))
).expanduser()
ENGINE_APP = Path(
    os.environ.get(
        "AGY_DICTATION_ENGINE_APP", Path.home() / "Applications/ProListen Voice Engine.app"
    )
).expanduser()
ENGINE_BOOTSTRAP = ENGINE_APP / "Contents/Resources/engine_bootstrap.py"
EDITOR_BRIDGE = BASE / "bin/prolisten-agy-export-prompt"
