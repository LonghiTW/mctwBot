import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from utils import bot_avatar


class _FakeAsset:
    def __init__(self, data):
        self._data = data

    async def read(self):
        return self._data


class BotAvatarTests(unittest.TestCase):
    def test_fetch_bot_avatar_returns_bytes(self):
        client = SimpleNamespace(user=SimpleNamespace(display_avatar=_FakeAsset(b"png-bytes")))

        result = asyncio.run(bot_avatar.fetch_bot_avatar(client))

        self.assertEqual(result, b"png-bytes")

    def test_fetch_bot_avatar_returns_none_without_user(self):
        self.assertIsNone(asyncio.run(bot_avatar.fetch_bot_avatar(SimpleNamespace(user=None))))

    def test_ensure_webhook_avatar_edits_webhook(self):
        client = SimpleNamespace(user=SimpleNamespace(display_avatar=_FakeAsset(b"png-bytes")))
        fake_webhook = SimpleNamespace(edit=AsyncMock())

        with patch.object(discord.Webhook, "from_url", return_value=fake_webhook) as from_url:
            asyncio.run(bot_avatar.ensure_webhook_avatar(client, "https://example.invalid/hook"))

        from_url.assert_called_once()
        self.assertEqual(fake_webhook.edit.await_args.kwargs["avatar"], b"png-bytes")

    def test_ensure_webhook_avatar_skips_without_avatar(self):
        client = SimpleNamespace(user=None)

        with patch.object(discord.Webhook, "from_url") as from_url:
            asyncio.run(bot_avatar.ensure_webhook_avatar(client, "https://example.invalid/hook"))

        from_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
