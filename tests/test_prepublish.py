import importlib.util
import json
import os
import subprocess
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

    def test_invalid_domain_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "privacy-policy.json").write_text(
                json.dumps({"public_commit_email_domains": ["*"]})
            )
            with self.assertRaises(ValueError):
                audit.commit_email_domains(root)
