import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from PySide6.QtGui import QImage, QImageWriter

from image_pipeline import ImageValidationError, validate_image_file, write_normalized_image


class ImagePipelineValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def save_image(self, filename, image, fmt):
        path = self.root / filename
        writer = QImageWriter(str(path), fmt.encode("ascii"))
        writer.setQuality(92)
        self.assertTrue(writer.write(image), writer.errorString())
        return path

    def test_png_signature_mime_dimensions_and_decode(self):
        path = self.save_image("valid.png", QImage(320, 180, QImage.Format_RGB32), "png")
        image, info = validate_image_file(path, stage="test PNG")
        self.assertEqual((info.mime_type, info.width, info.height), ("image/png", 320, 180))
        self.assertGreater(info.size_bytes, 24)
        self.assertFalse(image.isNull())
        self.assertTrue(info.signature_hex.startswith("89504e470d0a1a0a"))

    def test_jpeg_signature_mime_dimensions_and_decode(self):
        path = self.save_image("valid.jpg", QImage(128, 96, QImage.Format_RGB32), "jpeg")
        image, info = validate_image_file(path, stage="test JPEG")
        self.assertEqual((info.mime_type, info.width, info.height), ("image/jpeg", 128, 96))
        self.assertTrue(path.read_bytes().startswith(b"\xff\xd8\xff"))
        self.assertFalse(image.isNull())

    def test_text_responses_and_svg_are_rejected_and_logged(self):
        for filename, data in (
            ("svg.png", b'<svg xmlns="http://www.w3.org/2000/svg"><svg/></svg>'),
            ("html.png", b"<!doctype html><html>error</html>"),
            ("json.png", b'{"error":"bad request"}'),
            ("data-url.png", b"data:image/png;base64,iVBORw0KGgo="),
            ("double-data-url.png", b"data:image/png;base64,data:image/png;base64,iVBORw0KGgo="),
        ):
            path = self.root / filename
            path.write_bytes(data)
            log = self.root / "validation.log"
            with self.assertRaises(ImageValidationError):
                validate_image_file(path, stage="bad upscale response", log_path=log)
            self.assertIn("first32_hex", log.read_text(encoding="utf-8"))

    def test_filename_mime_mismatch_is_rejected(self):
        path = self.save_image("wrong-extension.jpg", QImage(32, 24, QImage.Format_RGB32), "png")
        with self.assertRaisesRegex(ImageValidationError, "does not match detected image/png"):
            validate_image_file(path, stage="extension mismatch")

    def test_corrupt_png_crc_is_rejected(self):
        path = self.save_image("corrupt.png", QImage(16, 16, QImage.Format_RGB32), "png")
        data = bytearray(path.read_bytes())
        # Corrupt the IHDR CRC, preserving a recognizable PNG signature.
        data[29] ^= 0x01
        path.write_bytes(data)
        with self.assertRaisesRegex(ImageValidationError, "CRC mismatch"):
            validate_image_file(path, stage="corrupt PNG")

    def test_oversized_upscale_is_rejected_before_pixel_decode(self):
        path = self.save_image("large.png", QImage(2, 2, QImage.Format_RGB32), "png")
        data = bytearray(path.read_bytes())
        width, height = 15360, 8640
        data[16:24] = struct.pack(">II", width, height)
        data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]) & 0xFFFFFFFF)
        path.write_bytes(data)
        with self.assertRaisesRegex(ImageValidationError, "pixel decode limit"):
            validate_image_file(path, stage="simulated 4x 4K result")

    def test_normalized_final_image_decodes_as_requested_png(self):
        source = self.save_image("source.png", QImage(640, 360, QImage.Format_RGB32), "png")
        image, _ = validate_image_file(source, stage="normalize input")
        result = self.root / "final.jpg"
        info = write_normalized_image(image, result, size=(3840, 2160), output_format="jpeg", quality=94)
        decoded, checked = validate_image_file(result, stage="normalized result")
        self.assertEqual((checked.mime_type, checked.width, checked.height), ("image/jpeg", 3840, 2160))
        self.assertFalse(decoded.isNull())
        self.assertEqual((info.width, info.height), (3840, 2160))


if __name__ == "__main__":
    unittest.main()
