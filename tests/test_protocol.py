import socket
import struct
import unittest

from blendk.errors import BlendkError
from blendk.protocol import MAX_FRAME_BYTES, receive_frame, send_frame


class ProtocolTests(unittest.TestCase):
    def test_round_trip_preserves_unicode(self) -> None:
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)

        send_frame(left, {"message": "blénd ✓"})

        self.assertEqual(receive_frame(right), {"message": "blénd ✓"})

    def test_rejects_invalid_size(self) -> None:
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        left.sendall(struct.pack(">I", MAX_FRAME_BYTES + 1))

        with self.assertRaisesRegex(BlendkError, "invalid protocol frame size"):
            receive_frame(right)

    def test_rejects_non_object_json(self) -> None:
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        payload = b"[]"
        left.sendall(struct.pack(">I", len(payload)) + payload)

        with self.assertRaisesRegex(BlendkError, "must be a JSON object"):
            receive_frame(right)


if __name__ == "__main__":
    unittest.main()

