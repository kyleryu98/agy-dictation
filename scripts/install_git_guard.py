#!/usr/bin/env python3
"""Install a repository-local privacy check without changing author identity."""

import argparse
import os
import shlex
import sys
from pathlib import Path
from prepublish_check import git

ROOT = Path(__file__).resolve().parents[1]
MARKER = "# AGY Dictation local privacy gate"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not args.apply:
        print(
            "Dry run: --apply installs a pre-commit privacy/identity check; no identity settings change."
        )
        return 0
    custom = git(ROOT, "config", "core.hooksPath", check=False).decode().strip()
    if custom:
        parser.error("A custom hooksPath exists; integrate the privacy check manually.")
    path = Path(git(ROOT, "rev-parse", "--git-path", "hooks/pre-commit").decode().strip())
    if not path.is_absolute():
        path = ROOT / path
    if path.is_symlink() or (path.exists() and MARKER not in path.read_text()):
        parser.error("An existing pre-commit hook is present; it was not overwritten.")
    git_override = os.environ.get("AGY_AUDIT_GIT")
    text = "#!/bin/sh\n" + MARKER + "\n"
    if git_override:
        text += "export AGY_AUDIT_GIT=" + shlex.quote(git_override) + "\n"
    text += (
        "exec "
        + shlex.join([sys.executable, "scripts/prepublish_check.py", "--check-identity"])
        + "\n"
    )
    path.write_text(text)
    path.chmod(0o700)
    print("Local pre-commit privacy gate installed. Git author name and email were not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
