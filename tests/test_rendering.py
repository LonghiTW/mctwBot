import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cogs.relay.rendering import (
    append_attachment_previews,
    format_referenced_message_text,
    is_image_attachment,
    strip_embed_urls_from_content,
)


class _ImageSlot:
    def __init__(self, url=None):
        self.url = url


class _Embed:
    def __init__(self, url=None, image_url=None, thumbnail_url=None):
        self.url = url
        self.image = _ImageSlot(image_url)
        self.thumbnail = _ImageSlot(thumbnail_url)


def _attachment(filename, url="https://cdn.example/file.png?sig=1", content_type=None):
    return SimpleNamespace(filename=filename, url=url, content_type=content_type)


class RenderingTests(unittest.TestCase):
    def test_format_referenced_message_text_without_attachments(self):
        self.assertEqual(format_referenced_message_text("hello", []), "hello")
        self.assertEqual(format_referenced_message_text("", []), "*(No text)*")
        self.assertEqual(format_referenced_message_text(None, []), "*(No text)*")

    def test_format_referenced_message_text_with_attachments(self):
        self.assertEqual(format_referenced_message_text("hello", [object()]), "🔗 hello")
        self.assertEqual(format_referenced_message_text("", [object()]), "🔗 click to see attachment")
        self.assertEqual(format_referenced_message_text(None, [object()]), "🔗 click to see attachment")

    def test_is_image_attachment_uses_content_type_or_extension(self):
        self.assertTrue(is_image_attachment(_attachment("file.bin", content_type="image/png")))
        self.assertTrue(is_image_attachment(_attachment("photo.webp", content_type=None)))
        self.assertFalse(is_image_attachment(_attachment("archive.zip", content_type="application/zip")))

    def test_append_attachment_previews_splits_images_and_urls(self):
        image = _attachment("one.png", "https://cdn.example/one.png?sig=1", "image/png")
        document = _attachment("doc.pdf", "https://cdn.example/doc.pdf?sig=2", "application/pdf")

        content, image_files = append_attachment_previews("body", [], [image, document])

        self.assertEqual(content, "body\nhttps://cdn.example/doc.pdf")
        self.assertEqual(image_files, [{
            "filename": "one.png",
            "url": "https://cdn.example/one.png?sig=1",
            "content_type": "image/png",
        }])

    def test_strip_embed_urls_from_content_keeps_klipy_urls(self):
        content = "before https://site.example/post https://www.klipy.com/gifs/abc after"
        embeds = [_Embed(url="https://site.example/post")]

        stripped = strip_embed_urls_from_content(content, embeds)

        self.assertEqual(stripped, "before https://www.klipy.com/gifs/abc after")


if __name__ == "__main__":
    unittest.main()
