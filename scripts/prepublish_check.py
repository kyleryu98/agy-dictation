#!/usr/bin/env python3
"""Local redacted privacy gate. Never uploads repository content or verifies tokens online."""

import argparse
import json
import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

LIMIT = 8 * 1024 * 1024
ARCHIVE_LIMIT = 64 * 1024 * 1024
PATTERNS = {
    "personal_home_path": re.compile(
        r"(?:/Users/[A-Za-z0-9_.-]+|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+)"
    ),
    "email_address": re.compile(r"[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "private_key": re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    "github_token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})"),
    "api_secret": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}"),
}
RUNTIME_NAMES = {
    "auth.json",
    "installation.json",
    "credentials.json",
    "transcript.json",
    "transcript-request.json",
    "last-transcript.txt",
    "engine-auth.json",
    "status.json",
    "hud-status.json",
}
BINARY_SUFFIXES = {".wav", ".mp3", ".aiff", ".webm", ".mp4", ".dmg", ".app", ".pem", ".key", ".pyc"}


def public_commit_email(email, domains=()):
    if not isinstance(email, str) or email.count("@") != 1:
        return False
    domain = email.rsplit("@", 1)[1].lower()
    return domain == "users.noreply.github.com" or domain in domains


def commit_email_domains(root):
    path = root / "privacy-policy.json"
    if not path.exists():
        return ()
    policy = json.loads(path.read_text(encoding="utf-8"))
    domains = policy.get("public_commit_email_domains", [])
    if not isinstance(domains, list) or any(
        not isinstance(x, str) or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", x) for x in domains
    ):
        raise ValueError("Invalid public commit email domain policy")
    return tuple(domains)


def scan_bytes(data, label, markers=(), public_commit_domains=()):
    issues = []
    if len(data) > LIMIT:
        return [{"path": label, "kind": "oversize_unscanned"}]
    if b"\0" in data:
        return [{"path": label, "kind": "binary_requires_review"}]
    text = data.decode("utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in PATTERNS.items():
            matches = list(pattern.finditer(line))
            if kind == "email_address":
                matches = [
                    m for m in matches if not public_commit_email(m.group(), public_commit_domains)
                ]
            if matches:
                issues.append({"path": label, "line": number, "kind": kind})
        if any(marker.casefold() in line.casefold() for marker in markers):
            issues.append({"path": label, "line": number, "kind": "personal_marker"})
    return issues


def scan_name(name):
    p = Path(name)
    if (
        p.name in RUNTIME_NAMES
        or p.suffix.lower() in BINARY_SUFFIXES
        or p.suffix.lower() in {".log", ".sock", ".pid", ".lock"}
    ):
        return [{"path": name, "kind": "runtime_or_private_artifact"}]
    if p.name == ".env" or (p.name.startswith(".env.") and p.name != ".env.example"):
        return [{"path": name, "kind": "environment_file"}]
    if p.name in {"INSTALL.txt", "runtime.json"}:
        return [{"path": name, "kind": "local_build_metadata"}]
    return []


def git(root, *args, check=True):
    executable = os.environ.get("AGY_AUDIT_GIT") or shutil.which("git")
    if not executable:
        raise RuntimeError("Git not available; set AGY_AUDIT_GIT")
    result = subprocess.run([executable, "-C", str(root), *args], capture_output=True)
    if check and result.returncode:
        raise RuntimeError("Git command failed; set AGY_AUDIT_GIT to a working Git executable")
    return result.stdout


def scan_repo(root, markers=(), check_identity=False):
    issues = []
    public_domains = commit_email_domains(root)
    seen_blobs = set()
    files = (
        git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        .decode()
        .split("\0")
    )
    for name in sorted(set(filter(None, files))):
        path = root / name
        issues.extend(scan_name(name))
        if path.is_symlink():
            issues.append({"path": name, "kind": "symlink_requires_review"})
            continue
        if path.is_file():
            if path.stat().st_size > LIMIT:
                issues.append({"path": name, "kind": "oversize_unscanned"})
            else:
                issues.extend(scan_bytes(path.read_bytes(), name, markers))

    def blob(oid, label):
        if oid in seen_blobs:
            return
        seen_blobs.add(oid)
        size = int(git(root, "cat-file", "-s", oid))
        if size > LIMIT:
            issues.append({"path": label, "kind": "oversize_unscanned"})
            return
        issues.extend(scan_bytes(git(root, "cat-file", "blob", oid), label, markers))

    # Scan the staged snapshot even when the working tree was subsequently cleaned.
    for entry in filter(None, git(root, "ls-files", "--stage", "-z").decode().split("\0")):
        meta, name = entry.split("\t", 1)
        mode, oid, _ = meta.split()
        if mode == "160000":
            issues.append({"path": name, "kind": "submodule_requires_review"})
        else:
            blob(oid, "index:" + name)
    objects = git(root, "rev-list", "--objects", "--all").decode().splitlines()
    for item in objects:
        oid, _, name = item.partition(" ")
        if git(root, "cat-file", "-t", oid).strip() == b"blob":
            blob(oid, "history:" + name)
    metadata = git(root, "log", "--all", "--format=%H%n%an%n%ae%n%cn%n%ce%n%B")
    issues.extend(scan_bytes(metadata, "history:commit-metadata", markers, public_domains))
    identity = {"checked": False}
    if check_identity:
        # Git's effective identity includes environment/command-line overrides.
        identities = []
        for variable in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            raw = git(root, "var", variable, check=False).decode().strip()
            match = re.fullmatch(r"(.+) <([^<>]+)> [0-9]+ [+-][0-9]{4}", raw)
            name, email = match.groups() if match else ("", "")
            identities.append(
                (
                    bool(name),
                    bool(email),
                    public_commit_email(email, public_domains),
                )
            )
        identity = {
            "checked": True,
            "name_configured": all(item[0] for item in identities),
            "email_configured": all(item[1] for item in identities),
            "approved_public_email": all(item[2] for item in identities),
        }
        if not identity["approved_public_email"]:
            issues.append({"path": "git:future-commit-author", "kind": "private_email_risk"})
        if not identity["name_configured"]:
            issues.append({"path": "git:future-commit-author", "kind": "missing_author_name"})
    return {
        "candidate_files": len(set(filter(None, files))),
        "git_objects_scanned": len(seen_blobs),
        "identity": identity,
        "findings": issues,
    }


def scan_archive(path, markers=()):
    issues = []
    total = 0

    def entry(name, data):
        nonlocal total
        total += len(data)
        if total > ARCHIVE_LIMIT:
            raise ValueError("Archive scan size limit exceeded")
        issues.extend(scan_name(name))
        issues.extend(scan_bytes(data, "artifact:" + name, markers))

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if info.file_size > LIMIT:
                    issues.append({"path": info.filename, "kind": "oversize_unscanned"})
                    continue
                if total + info.file_size > ARCHIVE_LIMIT:
                    raise ValueError("Archive scan size limit exceeded")
                entry(info.filename, archive.read(info))
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            for info in archive:
                if info.issym() or info.islnk():
                    issues.append({"path": info.name, "kind": "symlink_requires_review"})
                    continue
                if not info.isfile():
                    continue
                if info.size > LIMIT:
                    issues.append({"path": info.name, "kind": "oversize_unscanned"})
                    continue
                if total + info.size > ARCHIVE_LIMIT:
                    raise ValueError("Archive scan size limit exceeded")
                entry(info.name, archive.extractfile(info).read())
    else:
        raise ValueError("Only wheel/zip and tar source archives are supported")
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--personal-marker", action="append", default=[])
    parser.add_argument("--check-identity", action="store_true")
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    try:
        report = scan_repo(args.root.resolve(), args.personal_marker, args.check_identity)
        for artifact in args.artifact:
            report["findings"].extend(scan_archive(artifact, args.personal_marker))
    except (OSError, ValueError, RuntimeError):
        print(
            json.dumps(
                {
                    "error": "Audit could not finish; check Git, file access, and archive size. No file contents are printed."
                }
            )
        )
        return 2
    report["passed"] = not report["findings"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
