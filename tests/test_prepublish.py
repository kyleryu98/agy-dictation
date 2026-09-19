import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit", ROOT / "scripts/prepublish_check.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class PrivacyGateTests(unittest.TestCase):
    def test_findings_do_not_print_matched_data(self):
        private_email = "private-user" + chr(64) + "example.invalid"
        home = "/" + "Users" + "/" + "sample-user"
        key = "gh" + "p_" + "x" * 40
        result = audit.scan_bytes((private_email + "\n" + home + "\n" + key).encode(), "fixture")
        self.assertEqual(
            {x["kind"] for x in result}, {"email_address", "personal_home_path", "github_token"}
        )
        for sensitive in [private_email, home, key]:
            self.assertNotIn(sensitive, json.dumps(result))

    def test_archive_contents_are_scanned_without_extracting(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "fixture.whl"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "package/runtime.json",
                    json.dumps({"path": "/" + "Users" + "/" + "sample-user"}),
                )
            issues = audit.scan_archive(path)
            self.assertTrue(any(x["kind"] == "personal_home_path" for x in issues))
            self.assertFalse((Path(name) / "package").exists())

    def test_staged_secret_is_detected_even_when_worktree_is_clean(self):
        executable = os.environ.get("AGY_AUDIT_GIT")
        if not executable:
            import shutil

            executable = shutil.which("git")
        if not executable:
            self.skipTest("Git unavailable")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)

            def git(*args):
                return subprocess.run(
                    [executable, "-C", name, *args], capture_output=True, check=True
                )

            git("init")
            file = root / "code.txt"
            file.write_text("gh" + "p_" + "z" * 40)
            git("add", "code.txt")
            file.write_text("clean")
            with patch.dict(os.environ, {"AGY_AUDIT_GIT": executable}):
                report = audit.scan_repo(root)
            self.assertTrue(
                any(
                    x["path"] == "index:code.txt" and x["kind"] == "github_token"
                    for x in report["findings"]
                )
            )

    def test_noreply_identity_is_not_reported_as_private_email(self):
        value = ("123+example" + chr(64) + "users.noreply.github.com").encode()
        self.assertFalse(audit.scan_bytes(value, "metadata"))

    def test_effective_identity_checks_author_and_committer(self):
        safe = "123+tester" + chr(64) + "users.noreply.github.com"
        private = "private" + chr(64) + "example.invalid"

        def fake_git(root, *args, **kwargs):
            if args == ("var", "GIT_AUTHOR_IDENT"):
                return ("Tester <" + safe + "> 1 +0000").encode()
            if args == ("var", "GIT_COMMITTER_IDENT"):
                return ("Tester <" + private + "> 1 +0000").encode()
            return b""

        with patch.object(audit, "git", side_effect=fake_git):
            report = audit.scan_repo(ROOT, check_identity=True)
        self.assertFalse(report["identity"]["approved_public_email"])
        self.assertTrue(any(x["kind"] == "private_email_risk" for x in report["findings"]))
        self.assertNotIn(private, json.dumps(report))

    def test_public_business_email_allowed_in_commit_metadata_only(self):
        email = "author" + chr(64) + "example.org"
        self.assertFalse(
            audit.scan_bytes(
                email.encode(), "history:commit-metadata", public_commit_domains=("example.org",)
            )
        )
        self.assertTrue(audit.scan_bytes(email.encode(), "source.py"))

    def test_business_domain_match_is_exact(self):
        self.assertTrue(
            audit.public_commit_email("author" + chr(64) + "example.org", ("example.org",))
        )
        self.assertFalse(
            audit.public_commit_email("author" + chr(64) + "notexample.org", ("example.org",))
        )

    def test_public_github_bot_metadata_does_not_relax_source_or_identity(self):
        noreply = "noreply" + chr(64) + "github.com"
        support = "support" + chr(64) + "github.com"
        metadata = (noreply + "\nSigned-off-by: dependabot[bot] <" + support + ">").encode()
        self.assertFalse(audit.scan_bytes(metadata, "metadata", github_metadata=True))
        self.assertTrue(audit.scan_bytes(metadata, "source"))
        self.assertTrue(audit.scan_bytes(support.encode(), "metadata", github_metadata=True))
        self.assertFalse(audit.public_commit_email(noreply))

    def test_invalid_domain_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "privacy-policy.json").write_text(
                json.dumps({"public_commit_email_domains": ["*"]})
            )
            with self.assertRaises(ValueError):
                audit.commit_email_domains(root)

    def test_more_credentials_are_detected_without_echoing_values(self):
        samples = [
            "ya" + "29." + "x" * 40,
            "AK" + "IA" + "A" * 16,
            "xox" + "b-" + "1" * 30,
            "Bearer " + "y" * 32,
            "ey" + "J" + "x" * 20 + "." + "y" * 20 + "." + "z" * 20,
            "https://" + "user:" + "private-value" + "@example.invalid",
            'password' + ' = "' + 'not-a-real-password' + '"',
        ]
        for sample in samples:
            with self.subTest(kind=sample[:3]):
                result = audit.scan_bytes(sample.encode(), "fixture")
                self.assertTrue(result)
                self.assertNotIn(sample, json.dumps(result))

    def test_documented_placeholders_are_allowed(self):
        for value in ("YOUR_API_KEY", "${API_KEY}", "<your token>", "replace-me"):
            sample = 'api_key' + ' = "' + value + '"'
            self.assertFalse(audit.scan_bytes(sample.encode(), "fixture"))

    def test_runtime_names_and_audio_formats_are_blocked(self):
        for name in (
            "AUDIO.M4A", "clip.caf", "recordings/note.txt", "TRANSCRIPT-2026.txt",
            "auth.backup.json", "credentials.old.json", "backups/session.txt",
            "test.app/Contents/script.py", "DO_NOT_PUBLISH.txt", "capture.sqlite",
        ):
            self.assertTrue(audit.scan_name(name), name)
        for name in ("tests/test_export.py", "docs/testing.md", "src/agy_dictation/export_prompt.py"):
            self.assertFalse(audit.scan_name(name), name)

    def test_non_utf8_content_is_not_silently_skipped(self):
        self.assertEqual(audit.scan_bytes(b"\xff\xfe", "fixture")[0]["kind"],
                         "undecodable_requires_review")

    def test_deleted_historical_runtime_name_is_detected_even_for_reused_blob(self):
        executable = os.environ.get("AGY_AUDIT_GIT") or audit.shutil.which("git")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)

            def git(*args):
                return subprocess.run(
                    [executable, "-C", name, *args], capture_output=True, check=True
                )

            git("init")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture" + chr(64) + "users.noreply.github.com")
            (root / "safe.txt").write_text("synthetic sample")
            git("add", ".")
            git("commit", "-m", "safe")
            (root / "transcript-old.txt").write_text("synthetic sample")
            git("add", ".")
            git("commit", "-m", "fixture")
            git("rm", "transcript-old.txt")
            git("commit", "-m", "remove fixture")
            with patch.dict(os.environ, {"AGY_AUDIT_GIT": executable}):
                report = audit.scan_repo(root)
            self.assertTrue(any(x["path"] == "history:transcript-old.txt" for x in report["findings"]))

    def test_guard_installs_both_hooks_and_preserves_unrelated_hooks(self):
        spec = importlib.util.spec_from_file_location("guard", ROOT / "scripts/install_git_guard.py")
        guard = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"prepublish_check": audit}):
            spec.loader.exec_module(guard)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            hooks = root / "hooks"
            hooks.mkdir()

            def git(root, *args, **kwargs):
                return b"" if args[0] == "config" else args[-1].encode()

            with patch.object(guard, "ROOT", root), patch.object(guard, "git", side_effect=git):
                self.assertEqual(guard.main([]), 0)
                self.assertEqual(list(hooks.iterdir()), [])
                (hooks / "pre-push").write_text("unrelated hook")
                with self.assertRaises(SystemExit):
                    guard.main(["--apply"])
                self.assertFalse((hooks / "pre-commit").exists())
                self.assertEqual((hooks / "pre-push").read_text(), "unrelated hook")
                (hooks / "pre-push").unlink()
                self.assertEqual(guard.main(["--apply"]), 0)
                for hook in hooks.iterdir():
                    self.assertIn("--check-identity", hook.read_text())
                    self.assertEqual(hook.stat().st_mode & 0o777, 0o700)
