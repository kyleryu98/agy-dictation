"""Private POSIX runtime files under a trusted, user-selected parent directory.

Leaf directories/files must not be symlinks. Open descriptors pin the directory
and inode during operations. This is not isolation from compromised same-user code.
"""

import json
import math
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_PAYLOAD = 1024 * 1024


def _owned(info):
    if info.st_uid != os.geteuid():
        raise PermissionError("Runtime object has an unexpected owner")


@contextmanager
def private_directory(path):
    path = Path(path)
    # Existing ancestors are trusted configuration, not chmod targets.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _owned(os.fstat(fd))
        os.fchmod(fd, 0o700)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise PermissionError("Runtime directory is not private")
        yield fd
    finally:
        os.close(fd)


def ensure_private_dir(path):
    with private_directory(path):
        pass


def _name(name):
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:
        raise ValueError("Expected a single runtime filename")


def open_private_file(directory, name, flags=os.O_RDONLY, *, repair=False):
    _name(name)
    # Never truncate until ownership, type and link count have been checked.
    if flags & os.O_TRUNC:
        raise ValueError("Use atomic writes instead of truncating paths")
    fd = os.open(
        name,
        flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        0o600,
        dir_fd=directory,
    )
    try:
        info = os.fstat(fd)
        _owned(info)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PermissionError("Expected a singly linked regular file")
        if repair:
            os.fchmod(fd, 0o600)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            raise PermissionError("Runtime file is not private")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_bytes(fd, limit=MAX_PAYLOAD):
    if os.fstat(fd).st_size > limit:
        raise ValueError("Runtime payload is too large")
    with os.fdopen(os.dup(fd), "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Runtime payload is too large")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def read_json(directory, name, limit=MAX_PAYLOAD):
    fd = open_private_file(directory, name)
    try:
        result = json.loads(read_bytes(fd, limit), object_pairs_hook=_unique_object)
    finally:
        os.close(fd)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def validate_token(token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("Invalid request identity")
    return token


def validate_time(value, *, earliest=0):
    import time

    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not earliest <= value <= time.time() + 1
    ):
        raise ValueError("Invalid request timestamp")
    return value


def validate_text(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_PAYLOAD:
        raise ValueError("Invalid transcript text")
    if any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in text):
        raise ValueError("Transcript contains control characters")
    return text


def atomic_write_json(directory, name, payload):
    atomic_write_text(directory, name, json.dumps(payload, ensure_ascii=False, allow_nan=False))


def atomic_write_text(directory, name, text):
    _name(name)
    data = text.encode("utf-8")
    if len(data) > MAX_PAYLOAD:
        raise ValueError("Runtime payload is too large")
    # Refuse suspicious existing destinations, even though rename would not
    # follow a symlink. Nothing outside the exchange should be touched.
    try:
        old = open_private_file(directory, name, repair=True)
    except FileNotFoundError:
        pass
    else:
        os.close(old)
    temporary = ".exchange-" + uuid.uuid4().hex
    fd = open_private_file(
        directory,
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        repair=True,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        unlink(directory, temporary)


def unlink(directory, name):
    _name(name)
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


@contextmanager
def exchange_lock(directory):
    import fcntl

    fd = open_private_file(directory, ".transcript.lock", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
