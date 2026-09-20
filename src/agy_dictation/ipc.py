"""Bounded, same-user local IPC. Never include received data in errors or logs."""

import ctypes
import hmac
import json
import os
import re
import socket
import struct
import time
import unicodedata

COMMANDS = frozenset({"ping", "start", "begin", "finish", "cancel", "reset", "shutdown", "audio-status"})
MAX_REQUEST = 4096
MAX_RESPONSE = 262144
MAX_TRANSCRIPT = 32768
ERRORS = {
    "microphone_missing": "선택한 마이크가 연결되지 않았어요. 메뉴에서 마이크를 확인해 주세요.",
    "audio_stream_failed": "마이크 입력을 확인하지 못했어요. 연결을 확인하고 다시 녹음해 주세요.",
    "permission_required": "음성 엔진에 마이크 권한을 허용해 주세요.",
    "busy": "음성 엔진이 다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.",
    "cancelled": "녹음을 취소했습니다.",
    "protocol_error": "음성 엔진 응답을 확인하지 못했습니다.",
    "engine_error": "음성 엔진 작업에 실패했습니다. 다시 시도해 주세요.",
    "engine_unavailable": "음성 엔진 요청에 실패했습니다. 권한과 실행 상태를 확인해 주세요.",
    "cli_start_failed": "AGY CLI를 시작하지 못했습니다. CLI 설치 경로를 확인해 주세요.",
    "cli_exited": "AGY CLI가 종료됐습니다. 다시 시작해 주세요.",
    "cli_timeout": "AGY CLI 응답 대기 시간이 초과됐습니다.",
    "trust_required": "전사용 voice-session 폴더에서 AGY CLI를 직접 실행해 로그인과 폴더 신뢰를 확인해 주세요.",
    "unexpected_project": "예상하지 못한 작업 폴더 신뢰 요청입니다. 전사용 폴더 설정을 확인해 주세요.",
    "terms_required": "AGY CLI를 직접 실행해 이용약관을 확인해 주세요.",
    "login_required": "전사용 voice-session 폴더에서 AGY CLI를 직접 실행해 로그인과 초기 설정을 확인해 주세요.",
    "voice_unavailable": "AGY 음성입력 인증 또는 마이크 권한을 확인해 주세요.",
    "recording_required": "새 녹음을 시작해 주세요.",
    "transcription_timeout": "20초 안에 전사문을 받지 못했습니다. 녹음을 취소했습니다.",
}


class ProtocolError(ValueError):
    pass


class RemoteError(Exception):
    """An operation failure with only an allowlisted code and local fixed text."""

    def __init__(self, code="engine_error"):
        self.code = code if isinstance(code, str) and code in ERRORS else "engine_error"
        super().__init__(ERRORS[self.code])


def peer_uid(conn):
    if hasattr(conn, "getpeereid"):
        return conn.getpeereid()[0]
    if hasattr(socket, "SO_PEERCRED"):
        return struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.getpeereid
    function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    function.restype = ctypes.c_int
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    if function(conn.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise ProtocolError("Peer identity unavailable")
    return uid.value


def require_same_user(conn):
    if peer_uid(conn) != os.geteuid():
        raise ProtocolError("Peer identity rejected")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def receive(conn, limit, timeout):
    deadline = time.monotonic() + timeout
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProtocolError("Frame deadline exceeded")
        conn.settimeout(remaining)
        chunk = conn.recv(min(4096, limit + 1 - len(data)))
        if not chunk:
            raise ProtocolError("Incomplete frame")
        data.extend(chunk)
        if len(data) > limit:
            raise ProtocolError("Frame too large")
        if b"\n" in chunk:
            if data.count(b"\n") != 1 or not data.endswith(b"\n"):
                raise ProtocolError("Multiple frames are not allowed")
            try:
                value = json.loads(data, object_pairs_hook=_unique_object)
            except (ValueError, UnicodeError):
                raise ProtocolError("Invalid frame") from None
            if not isinstance(value, dict):
                raise ProtocolError("Object required")
            return value


def send(conn, payload, limit, timeout=5):
    encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > limit:
        raise ProtocolError("Frame too large")
    conn.settimeout(timeout)
    conn.sendall(encoded)


def validate_token(token):
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None:
        raise ProtocolError("Invalid capability")
    return token


def request_command(payload, token):
    if set(payload) != {"command", "token"}:
        raise ProtocolError("Invalid request fields")
    presented = validate_token(payload["token"])
    if not hmac.compare_digest(presented, validate_token(token)):
        raise ProtocolError("Invalid capability")
    command = payload["command"]
    if not isinstance(command, str) or command not in COMMANDS:
        raise ProtocolError("Unknown command")
    return command


def response_text(payload):
    if payload.get("ok") is True and set(payload) == {"ok", "text"}:
        text = payload["text"]
        if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT:
            raise ProtocolError("Invalid transcript")
        return text
    if payload.get("ok") is False and set(payload) == {"ok", "code"}:
        code = payload["code"]
        if isinstance(code, str) and code in ERRORS:
            raise RemoteError(code)
    raise ProtocolError("Invalid response")


def insertion_text(text):
    if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT:
        raise ProtocolError("Invalid transcript")
    text = text.replace("\r\n", " ").translate(
        str.maketrans({"\r": " ", "\n": " ", "\t": " ", "\u2028": " ", "\u2029": " "})
    )
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cc", "Cs"} or (category == "Cf" and char not in {"\u200c", "\u200d"}):
            raise ProtocolError("Control characters are not accepted")
    return text


def require_server_socket(path):
    import stat

    info = path.lstat()
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ProtocolError("Unexpected engine socket")
