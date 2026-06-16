import hashlib
import json
import os
import tempfile
import unittest

import fixture_db  # noqa: E402  (sets sys.path to src)
from fixture_db import build_fixture

from imessage_export.export import run_export
from imessage_export.pdfwriter import PdfBuilder


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmp, "chat.db")
        cls.info = build_fixture(cls.db_path)
        cls.out = os.path.join(cls.tmp, "exhibit")
        cls.db_sha_before = _sha256(cls.db_path)
        cls.manifest = run_export(
            db_path=cls.db_path,
            chat_rowid=1,
            out_dir=cls.out,
            operator="J. Doe",
            case_reference="Test v. Test",
        )

    def test_all_outputs_exist(self):
        for name in ("conversation.json", "conversation.html",
                     "conversation.pdf", "manifest.json", "manifest.txt"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)

    def test_source_unchanged_by_export(self):
        self.assertEqual(_sha256(self.db_path), self.db_sha_before)

    def test_pdf_is_well_formed(self):
        with open(os.path.join(self.out, "conversation.pdf"), "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertIn(b"%%EOF", data[-16:])
        self.assertIn(b"/Type /Page", data)

    def test_html_contains_text_and_unicode(self):
        with open(os.path.join(self.out, "conversation.html"),
                  encoding="utf-8") as f:
            html = f.read()
        self.assertIn("Hey, are we still on for tomorrow?", html)
        self.assertIn(self.info["attributed_text"], html)  # emoji preserved
        self.assertIn(self.manifest["source"]["sha256"], html)

    def test_json_roundtrip(self):
        with open(os.path.join(self.out, "conversation.json"),
                  encoding="utf-8") as f:
            payload = json.load(f)
        conv = payload["conversation"]
        self.assertEqual(conv["message_count"], 4)
        texts = [m["text"] for m in conv["messages"]]
        self.assertIn(self.info["attributed_text"], texts)
        self.assertEqual(payload["export_metadata"]["operator"], "J. Doe")

    def test_manifest_hashes_match_files(self):
        with open(os.path.join(self.out, "manifest.json"),
                  encoding="utf-8") as f:
            manifest = json.load(f)
        for o in manifest["outputs"]:
            actual = _sha256(os.path.join(self.out, o["name"]))
            self.assertEqual(actual, o["sha256"], o["name"])
        self.assertEqual(manifest["source"]["sha256"], self.db_sha_before)
        self.assertEqual(manifest["conversation"]["message_count"], 4)

    def test_manifest_text_human_readable(self):
        with open(os.path.join(self.out, "manifest.txt"),
                  encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("FORENSIC MANIFEST", txt)
        self.assertIn(self.db_sha_before, txt)
        self.assertIn("Test v. Test", txt)


class PdfWriterTests(unittest.TestCase):
    def test_multipage_pdf(self):
        pdf = PdfBuilder()
        pdf.set_header(["HEADER LINE"])
        pdf.title("Title")
        for i in range(400):  # force multiple pages
            pdf.line(f"line number {i} " + "word " * 10)
        data = pdf.build()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertIn(b"%%EOF", data[-16:])
        self.assertGreaterEqual(data.count(b"/Type /Page "), 2)

    def test_unicode_placeholder_in_pdf(self):
        pdf = PdfBuilder()
        pdf.line("emoji ✅ here")
        data = pdf.build()
        self.assertIn(b"[U+2705]", data)


if __name__ == "__main__":
    unittest.main()
