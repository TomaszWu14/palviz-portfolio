import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from ui.zaria_attachments import build_payload, last_message_content, MAX_BYTES


def _xlsx_bytes():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["sku", "qty"])
    ws.append(["A1", 7])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _docx_bytes(text="Halo świat"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


class ZariaAttachmentsTest(SimpleTestCase):
    def _files(self):
        return [
            SimpleUploadedFile("foto.png", b"\x89PNG\r\n\x1a\nfake", content_type="image/png"),
            SimpleUploadedFile("skan.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
            SimpleUploadedFile("dane.xlsx", _xlsx_bytes()),
            SimpleUploadedFile("pismo.docx", _docx_bytes()),
        ]

    def test_anthropic_native_blocks_and_text(self):
        blocks, text, names, warnings = build_payload(self._files(), "anthropic")
        types = [b["type"] for b in blocks]
        self.assertEqual(types.count("image"), 1)
        self.assertEqual(types.count("document"), 1)   # PDF natywnie
        self.assertIn("sku", text)                     # Excel wyekstrahowany
        self.assertIn("Halo świat", text)              # Word wyekstrahowany
        self.assertEqual(len(names), 4)
        self.assertEqual(warnings, [])
        payload = last_message_content("Zrób audyt", blocks, text)
        self.assertIsInstance(payload, list)
        self.assertEqual(payload[-1]["type"], "text")

    def test_non_vision_skips_media_keeps_text(self):
        blocks, text, names, warnings = build_payload(self._files(), "ollama")
        self.assertEqual(blocks, [])                   # brak vision → bez obrazu/PDF
        self.assertIn("sku", text)
        self.assertIn("Halo świat", text)
        self.assertTrue(any("obraz" in w for w in warnings))
        self.assertTrue(any("PDF" in w for w in warnings))
        # bez media → zwykły string
        self.assertIsInstance(last_message_content("hej", blocks, text), str)

    def test_oversize_skipped(self):
        big = SimpleUploadedFile("big.pdf", b"x" * (MAX_BYTES + 1), content_type="application/pdf")
        blocks, text, names, warnings = build_payload([big], "anthropic")
        self.assertEqual(blocks, [])
        self.assertTrue(any("10 MB" in w for w in warnings))
