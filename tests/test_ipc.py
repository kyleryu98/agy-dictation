import os
import socket
import unittest
from unittest.mock import patch
from agy_dictation import ipc


class IPCTests(unittest.TestCase):
    def test_same_user_peer(self):
        left, right = socket.socketpair()
        with left, right:
            ipc.require_same_user(left)
            with patch.object(ipc, "peer_uid", return_value=os.geteuid() + 1):
                with self.assertRaises(ipc.ProtocolError):
                    ipc.require_same_user(left)

    def test_bounded_frame_and_single_request(self):
        for data, limit in [(b"x" * 17, 16), (b"{}\n{}\n", 100), (b"[]\n", 100)]:
            left, right = socket.socketpair()
            with left, right:
                right.sendall(data)
                with self.assertRaises(ipc.ProtocolError):
                    ipc.receive(left, limit, 0.2)

    def test_valid_frame(self):
        left, right = socket.socketpair()
        with left, right:
            ipc.send(right, {"ok": True, "text": "한글"}, ipc.MAX_RESPONSE)
            self.assertEqual(ipc.response_text(ipc.receive(left, ipc.MAX_RESPONSE, 1)), "한글")

    def test_capability_and_commands_are_required(self):
        token = "a" * 64
        self.assertEqual(ipc.request_command({"command": "ping", "token": token}, token), "ping")
        for payload in [
            {"command": "begin", "token": "b" * 64},
            {"command": "execute", "token": token},
            {"command": "ping", "token": token, "extra": 1},
        ]:
            with self.assertRaises(ipc.ProtocolError):
                ipc.request_command(payload, token)

    def test_server_cannot_supply_arbitrary_error_text(self):
        with self.assertRaises(ipc.ProtocolError) as caught:
            ipc.response_text({"ok": False, "error": "sensitive contents"})
        self.assertNotIn("sensitive contents", str(caught.exception))

    def test_known_operation_errors_preserve_code_and_fixed_local_text(self):
        for code, message in ipc.ERRORS.items():
            with self.subTest(code=code):
                with self.assertRaises(ipc.RemoteError) as caught:
                    ipc.response_text({"ok": False, "code": code})
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(str(caught.exception), message)

    def test_unknown_errors_and_extra_messages_are_not_trusted(self):
        for payload in (
            {"ok": False, "code": "SECRET"},
            {"ok": False, "code": "trust_required", "message": "SECRET"},
            {"ok": False, "code": ["SECRET"]},
        ):
            with self.assertRaises(ipc.ProtocolError) as caught:
                ipc.response_text(payload)
            self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(ipc.RemoteError("SECRET").code, "engine_error")

    def test_shutdown_still_requires_the_capability(self):
        token = "a" * 64
        self.assertEqual(
            ipc.request_command({"command": "shutdown", "token": token}, token), "shutdown"
        )
        with self.assertRaises(ipc.ProtocolError):
            ipc.request_command({"command": "shutdown", "token": "b" * 64}, token)

    def test_control_characters_rejected_before_insertion(self):
        for char in [chr(0), chr(3), chr(27), chr(127), "\u202e", "\ud800"]:
            with self.assertRaises(ipc.ProtocolError):
                ipc.insertion_text("before" + char + "after")
        self.assertEqual(ipc.insertion_text("one\r\ntwo\tthree"), "one two three")
        self.assertEqual(ipc.insertion_text("한글 👨\u200d👩"), "한글 👨\u200d👩")

    def test_transcript_size_is_bounded(self):
        with self.assertRaises(ipc.ProtocolError):
            ipc.insertion_text("x" * (ipc.MAX_TRANSCRIPT + 1))

    def test_duplicate_json_fields_rejected(self):
        left, right = socket.socketpair()
        with left, right:
            right.sendall(b'{"command":"start","command":"finish"}\n')
            with self.assertRaises(ipc.ProtocolError):
                ipc.receive(left, ipc.MAX_REQUEST, 1)
