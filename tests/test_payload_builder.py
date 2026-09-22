import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from discord import StickerFormatType

from cogs.relay.payload_builder import RelayPayloadBuilder, _sticker_content_type, _sticker_filename


def _message(content):
    return SimpleNamespace(
        id=123,
        content=content,
        channel=SimpleNamespace(id=456),
        guild=SimpleNamespace(id=789),
        message_snapshots=[],
        poll=None,
        embeds=[],
        attachments=[],
        stickers=[],
        reference=None,
    )


class StickerPayloadTests(unittest.TestCase):
    def test_klipy_urls_are_left_for_native_webhook_unfurling(self):
        async def resolve_emojis(content, embeds, guild):
            return content, embeds

        bot = SimpleNamespace(get_guild=lambda guild_id: SimpleNamespace(id=guild_id))
        builder = RelayPayloadBuilder(bot, resolve_emojis)
        original = _message("https://klipy.com/gifs/cat-meme-wave-emoji")
        builder._download_files_for_upload = AsyncMock(return_value=(original.content, []))

        payload, _meta, files = asyncio.run(
            builder.build(
                original=original,
                target={"guild_id": "789", "channel_id": "456", "group_id": 1},
                group={"group_name": "test"},
                username="tester",
                avatar_url="https://example.com/avatar.png",
                content=original.content,
                reply_embed=None,
                is_forward=False,
                exec_id="exec",
                thread_route={},
            )
        )

        self.assertEqual(payload["content"], "https://klipy.com/gifs/cat-meme-wave-emoji")
        self.assertEqual(payload["embeds"], [])
        self.assertEqual(files, [])

    def test_reply_embed_is_forwarded(self):
        async def resolve_emojis(content, embeds, guild):
            return content, embeds

        bot = SimpleNamespace(get_guild=lambda guild_id: SimpleNamespace(id=guild_id))
        builder = RelayPayloadBuilder(bot, resolve_emojis)
        reply_embed = SimpleNamespace(to_dict=lambda: {"description": "Replying to message"})
        original = _message("reply content")

        payload, _meta, _files = asyncio.run(
            builder.build(
                original=original,
                target={"guild_id": "789", "channel_id": "456", "group_id": 1},
                group={"group_name": "test"},
                username="tester",
                avatar_url="https://example.com/avatar.png",
                content=original.content,
                reply_embed=reply_embed,
                is_forward=False,
                exec_id="exec",
                thread_route={},
            )
        )

        self.assertEqual(payload["embeds"], [{"description": "Replying to message"}])

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
