import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ci_guest


class GuestContainerTests(unittest.TestCase):
    def setUp(self):
        self.data = b"\0" * 16 + b"NRO0" + b"test guest" * 20
        self.key = bytes(range(32))
        self.pin = patch.object(ci_guest, "PINNED_SHA256", hashlib.sha256(self.data).hexdigest())
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def test_authenticated_round_trip_and_nondeterministic_nonce(self):
        first = ci_guest.seal(self.data, self.key)
        second = ci_guest.seal(self.data, self.key)
        self.assertNotEqual(first, second)
        self.assertEqual(ci_guest.open_container(first, self.key), self.data)
        report = ci_guest.inspect_container(first)
        self.assertEqual(report["plaintext_bytes"], len(self.data))
        self.assertNotIn(self.key.hex(), str(report))

    def test_wrong_key_and_modified_ciphertext_are_rejected(self):
        packed = ci_guest.seal(self.data, self.key)
        for candidate, key in ((packed, bytes(32)), (packed[:-1] + bytes([packed[-1] ^ 1]), self.key)):
            with self.subTest(key=key == self.key), self.assertRaisesRegex(ValueError, "authentication failed"):
                ci_guest.open_container(candidate, key)

    def test_bad_container_header_digest_and_truncation_are_rejected(self):
        packed = ci_guest.seal(self.data, self.key)
        for candidate in (b"invalid", packed[1:], packed[:-1], b"X" + packed[1:], packed[:17] + bytes(32) + packed[49:]):
            with self.assertRaises(ValueError):
                ci_guest.inspect_container(candidate)

    def test_unpinned_data_cannot_be_sealed(self):
        with self.assertRaisesRegex(ValueError, "pinned original"):
            ci_guest.seal(self.data + b"changed", self.key)


if __name__ == "__main__":
    unittest.main()
