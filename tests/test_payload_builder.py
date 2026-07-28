from types import SimpleNamespace
import unittest

from discord import StickerFormatType

from cogs.relay.payload_builder import _sticker_content_type, _sticker_filename


class StickerPayloadTests(unittest.TestCase):
    def test_sticker_content_type_for_animated_formats(self):
        self.assertEqual(_sticker_content_type(StickerFormatType.gif), "image/gif")
        self.assertEqual(_sticker_content_type(StickerFormatType.apng), "image/apng")
        self.assertEqual(_sticker_content_type(StickerFormatType.png), "image/png")

    def test_sticker_filename_sanitizes_name_and_uses_extension(self):
        gif = SimpleNamespace(id=123, name="panda wow!", format=StickerFormatType.gif)
        png = SimpleNamespace(id=456, name=" ", format=StickerFormatType.apng)

        self.assertEqual(_sticker_filename(gif), "panda_wow_123.gif")
        self.assertEqual(_sticker_filename(png), "sticker_456.png")


if __name__ == "__main__":
    unittest.main()
