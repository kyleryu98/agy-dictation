import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_doctor_never_creates_runtime_or_launches_apps(self):
        with tempfile.TemporaryDirectory() as name:
            data = Path(name) / "runtime"
            env = os.environ.copy()
            env["AGY_DICTATION_DATA_DIR"] = str(data)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/doctor.py")],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("account_and_microphone", json.loads(result.stdout))
            self.assertFalse(data.exists())

    def test_launch_manifest_uses_explicit_python_and_scoped_label(self):
        spec = importlib.util.spec_from_file_location("builder", ROOT / "scripts/build_macos.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        manifest = builder.launch_agent(Path("/test/python"), Path("/test/src"))
        self.assertEqual(
            manifest["ProgramArguments"], ["/test/python", "-m", "agy_dictation.macos.service"]
        )
        self.assertEqual(manifest["Label"], "com.prolisten.agy-dictation")
        self.assertTrue(manifest["RunAtLoad"])
        self.assertNotIn("com.antigravity.dictation", str(manifest))

    def test_source_contains_no_personal_machine_paths(self):
        for path in (ROOT / "src").rglob("*.py"):
            self.assertNotIn("/Users/", path.read_text(), str(path))
