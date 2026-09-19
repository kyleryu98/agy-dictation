"""Export the AGY editor buffer without submitting it or using the clipboard."""

import argparse
import hmac
import os
import sys
import time
from pathlib import Path

from .secure_files import (
    atomic_write_json,
    exchange_lock,
    open_private_file,
    private_directory,
    read_bytes,
    read_json,
    unlink,
    validate_text,
    validate_time,
    validate_token,
)


def export(draft: Path, output: Path) -> None:
    # Each CLI process gets a fresh session identity. An editor left over from
    # a previous recording cannot acquire the next recording's request token.
    session = validate_token(os.environ.get("AGY_DICTATION_SESSION"))
    with private_directory(output.parent) as directory, exchange_lock(directory):
        request = read_json(directory, "transcript-request.json", 4096)
        token = validate_token(request.get("token"))
        expected_session = validate_token(request.get("session"))
        if not hmac.compare_digest(session, expected_session):
            raise ValueError("Stale editor session")
        validate_time(request.get("time"), earliest=time.time() - 30)
        # Drafts belong to AGY, so do not chmod their parent directory. Pin the
        # opened inode and refuse symlinks, hardlinks, devices and FIFOs.
        parent = os.open(draft.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = open_private_file(parent, draft.name, os.O_RDWR, repair=True)
            try:
                original = read_bytes(fd)
                text = validate_text(original.decode("utf-8"))
                # Consume before publishing: a failure preserves the draft but
                # cannot allow duplicate callbacks to overwrite a delivered result.
                unlink(directory, "transcript-request.json")
                atomic_write_json(
                    directory,
                    output.name,
                    {
                        "text": text,
                        "time": time.time(),
                        "token": token,
                        "session": session,
                    },
                )
                # Clear only this inode, and only if its contents are unchanged.
                os.lseek(fd, 0, os.SEEK_SET)
                if read_bytes(fd) != original:
                    raise ValueError("Draft changed during export")
                os.ftruncate(fd, 0)
            finally:
                os.close(fd)
        finally:
            os.close(parent)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft", type=Path)
    args = parser.parse_args(argv)
    destination = os.environ.get("AGY_DICTATION_EXPORT")
    if not destination:
        parser.error("AGY_DICTATION_EXPORT must be provided by the dictation service")
    try:
        export(args.draft, Path(destination))
    except Exception:
        # Decoder/parser errors may contain transcript bytes. Never print the
        # exception or traceback into the provider PTY or its logs.
        print("Dictation export failed; draft was not confirmed cleared.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
