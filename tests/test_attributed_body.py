import unittest

from fixture_db import make_attributed_body  # noqa: E402  (path set in fixture_db)

from imessage_export.attributed_body import decode_attributed_body


class AttributedBodyTests(unittest.TestCase):
    def test_short_string(self):
        blob = make_attributed_body("hello world")
        self.assertEqual(decode_attributed_body(blob), "hello world")

    def test_unicode_string(self):
        text = "café ✅ déjà vu"
        self.assertEqual(decode_attributed_body(make_attributed_body(text)), text)

    def test_long_string_uses_two_byte_length(self):
        text = "x" * 250  # forces the 0x81 + 2-byte length encoding
        self.assertEqual(decode_attributed_body(make_attributed_body(text)), text)

    def test_empty_string(self):
        self.assertEqual(decode_attributed_body(make_attributed_body("")), "")

    def test_no_marker_returns_none(self):
        self.assertIsNone(decode_attributed_body(b"\x04\x0bstreamtyped random"))

    def test_none_and_empty(self):
        self.assertIsNone(decode_attributed_body(None))
        self.assertIsNone(decode_attributed_body(b""))

    def test_control_heavy_decode_rejected(self):
        blob = b"\x04\x0bstreamtyped NSString\x01\x94\x84\x01+\x04\x01\x02\x03\x04"
        self.assertIsNone(decode_attributed_body(blob))


if __name__ == "__main__":
    unittest.main()
