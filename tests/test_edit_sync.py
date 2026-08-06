"""Tests for edit sync keeping the reply embed across message edits."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.relay.edit_sync import EditSync


def _replied_message():
    return SimpleNamespace(
        id=50,
        content="original reply target",
        attachments=[],
        edited_at=None,
        message_snapshots=[],
        author=SimpleNamespace(
            display_name="replied_user",
            display_avatar=SimpleNamespace(url="https://avatar/x.png"),
        ),
        jump_url="https://discord.com/channels/1/2/50",
    )


def _edited_message(replied):
    return SimpleNamespace(
        guild=SimpleNamespace(id=1, name="Test Guild"),
        type=discord.MessageType.reply,
        author=SimpleNamespace(id=123, bot=False, display_name="tester"),
        webhook_id=None,
        application_id=None,
        id=100,
        content="edited content",
        embeds=[],
        attachments=[],
        reference=SimpleNamespace(message_id=50),
        message_snapshots=[],
        channel=SimpleNamespace(
            id=2,
            fetch_message=AsyncMock(return_value=replied),
        ),
    )


def _mock_db():
    db = SimpleNamespace()

    def fetchone(sql, params=()):
        if "FROM linked_channels WHERE channel_id" in sql:
            return {
                "process_bot_messages": False,
                "brand_name": None,
                "group_id": 1,
            }
        if "FROM relay_groups WHERE group_id" in sql:
            return {"owner_user_id": None}
        if "FROM group_filters" in sql:
            return None
        if "FROM relayed_messages WHERE relayed_message_id" in sql:
            return None  # replied message is not itself a relayed copy
        if "FROM relayed_messages WHERE original_message_id" in sql and "relayed_channel_id" in sql:
            return None  # no copy link
        return None

    db.fetchone = fetchone
    db.fetchall = lambda sql, params=(): []
    return db


class EditSyncReplyEmbedTests(unittest.TestCase):
    async def _run_edit(self, message, resolve_emojis):
        bot = SimpleNamespace(
            user=None,
            get_channel=lambda cid: SimpleNamespace(guild=SimpleNamespace(id=1, name="Target")),
        )
        sync = EditSync(bot, resolve_emojis)
        sync.webhooks = SimpleNamespace(edit_message=AsyncMock())
        with patch("cogs.relay.edit_sync.DatabaseManager", return_value=_mock_db()), \
             patch("cogs.relay.edit_sync.RelayMessageStore") as Store:
            store = Store.return_value
            store.has_original.return_value = True
            store.relayed_for_original.return_value = [
                {"relayed_message_id": "200", "relayed_channel_id": "999"}
            ]
            await sync.sync_edit(message, {discord.MessageType.default, discord.MessageType.reply})
        return sync.webhooks.edit_message

    def test_reply_embed_kept_on_edit(self):
        async def resolve_emojis(content, embeds, guild):
            return content, embeds

        edit_message = _run_wrapper(self._run_edit(_edited_message(_replied_message()), resolve_emojis))

        edit_message.assert_awaited_once()
        kwargs = edit_message.await_args.kwargs
        embeds = kwargs["embeds"]
        self.assertTrue(embeds, "reply embed should still be present after edit")
        self.assertEqual(embeds[0].author.name, "Replying to replied_user")
        self.assertIn("original reply target", embeds[0].description)
        self.assertEqual(kwargs["content"], "edited content")

    def test_reply_embed_marked_deleted_when_reference_gone(self):
        async def resolve_emojis(content, embeds, guild):
            return content, embeds

        replied = _replied_message()
        message = _edited_message(replied)
        message.channel.fetch_message = AsyncMock(side_effect=RuntimeError("gone"))

        edit_message = _run_wrapper(self._run_edit(message, resolve_emojis))

        edit_message.assert_awaited_once()
        embeds = edit_message.await_args.kwargs["embeds"]
        self.assertEqual(embeds[0].description, "*Replying to a deleted message.*")

    def test_no_reply_embed_when_plain_message(self):
        async def resolve_emojis(content, embeds, guild):
            return content, embeds

        replied = _replied_message()
        message = _edited_message(replied)
        message.type = discord.MessageType.default
        message.reference = None

        edit_message = _run_wrapper(self._run_edit(message, resolve_emojis))

        edit_message.assert_awaited_once()
        self.assertEqual(edit_message.await_args.kwargs["embeds"], [])


def _run_wrapper(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
