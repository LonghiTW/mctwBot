"""Tests for the bot-admin relay chain delete command."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.relay.message_sync import MessageSync
from cogs.relay.relay_cog import RelayCog


def _run(coro):
    return asyncio.run(coro)


class _FakeDb:
    def __init__(self, original_id="100", original_channel_id="10", relayed=None, allow_reverse_delete=False):
        self.original_id = original_id
        self.original_channel_id = original_channel_id
        self.allow_reverse_delete = allow_reverse_delete
        self.relayed = relayed or [
            {"relayed_message_id": "200", "relayed_channel_id": "20"},
            {"relayed_message_id": "300", "relayed_channel_id": "30"},
        ]
        self.mappings_deleted = False

    def fetchone(self, query, params=()):
        if "SELECT allow_reverse_delete" in query:
            return {"allow_reverse_delete": int(self.allow_reverse_delete)}
        if "WHERE relayed_message_id = ?" in query:
            for row in self.relayed:
                if row["relayed_message_id"] == params[0]:
                    return {
                        "original_message_id": self.original_id,
                        "original_channel_id": self.original_channel_id,
                    }
            return None
        if "WHERE original_message_id = ? ORDER BY id" in query and params[0] == self.original_id:
            return {"original_channel_id": self.original_channel_id}
        return None

    def fetchall(self, query, params=()):
        if "WHERE original_message_id = ?" in query and params[0] == self.original_id:
            return list(self.relayed)
        return []

    def execute(self, query, params=()):
        if query.startswith("DELETE FROM relayed_messages"):
            self.mappings_deleted = True

    def commit(self):
        pass


class _FakeOriginalMessage:
    def __init__(self):
        self.deleted = False

    async def delete(self):
        self.deleted = True


class _FakeChannel:
    def __init__(self, message):
        self.message = message

    async def fetch_message(self, message_id):
        return self.message


class RelayChainDeleteTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDb()
        self.original_message = _FakeOriginalMessage()
        self.bot = SimpleNamespace(
            fetch_channel=AsyncMock(return_value=_FakeChannel(self.original_message))
        )
        self.sync = MessageSync(self.bot)
        self.deleted_copies = []

        async def delete_message(_db, channel_id, message_id):
            self.deleted_copies.append((str(channel_id), str(message_id)))

        self.sync.webhooks.delete_message = AsyncMock(side_effect=delete_message)
        self.sync.mark_replied_message_deleted = AsyncMock()

    def _run_chain(self, message_id):
        with patch("cogs.relay.message_sync.DatabaseManager", return_value=self.db):
            return _run(self.sync.delete_relay_chain(message_id))

    def test_original_id_deletes_source_and_all_copies(self):
        result = self._run_chain("100")

        self.assertEqual(result, {"found": True, "deleted": 3, "failed": []})
        self.assertTrue(self.original_message.deleted)
        self.assertEqual(self.deleted_copies, [("20", "200"), ("30", "300")])
        self.assertTrue(self.db.mappings_deleted)
        self.sync.mark_replied_message_deleted.assert_awaited_once()

    def test_relayed_id_resolves_and_deletes_entire_chain(self):
        result = self._run_chain("200")

        self.assertEqual(result["deleted"], 3)
        self.assertTrue(self.original_message.deleted)
        self.assertEqual(len(self.deleted_copies), 2)

    def test_unknown_id_does_not_delete_anything(self):
        result = self._run_chain("999")

        self.assertFalse(result["found"])
        self.assertFalse(self.original_message.deleted)
        self.assertEqual(self.deleted_copies, [])

    def test_partial_failure_keeps_mappings_for_retry(self):
        self.sync.webhooks.delete_message.side_effect = [None, RuntimeError("webhook unavailable")]

        result = self._run_chain("100")

        self.assertEqual(len(result["failed"]), 1)
        self.assertFalse(self.db.mappings_deleted)
        self.sync.mark_replied_message_deleted.assert_not_awaited()

    def test_command_requires_bot_admin_feature(self):
        cog = object.__new__(RelayCog)
        cog.message_sync = self.sync
        ctx = SimpleNamespace(author=SimpleNamespace(id=123), send=AsyncMock())

        with patch("cogs.relay.relay_cog.bot_admin_has_feature", return_value=False):
            _run(RelayCog.relaydelete.callback(cog, ctx, "100"))

        ctx.send.assert_awaited_once()
        self.bot.fetch_channel.assert_not_awaited()

    def test_command_with_feature_calls_chain_delete(self):
        cog = object.__new__(RelayCog)
        cog.message_sync = self.sync
        cog.message_sync.delete_relay_chain = AsyncMock(
            return_value={"found": True, "deleted": 3, "failed": []}
        )
        ctx = SimpleNamespace(author=SimpleNamespace(id=123), send=AsyncMock())

        with patch("cogs.relay.relay_cog.bot_admin_has_feature", return_value=True):
            _run(RelayCog.relaydelete.callback(cog, ctx, "100"))

        cog.message_sync.delete_relay_chain.assert_awaited_once_with("100")
        self.assertIn("3", ctx.send.await_args.args[0])

    def test_channel_flag_false_disables_automatic_reverse_delete(self):
        with patch("cogs.relay.message_sync.DatabaseManager", return_value=self.db):
            handled = _run(self.sync.sync_reverse_delete("200"))

        self.assertTrue(handled)
        self.bot.fetch_channel.assert_not_awaited()

    def test_channel_flag_true_keeps_automatic_reverse_delete(self):
        self.db.allow_reverse_delete = True

        with patch("cogs.relay.message_sync.DatabaseManager", return_value=self.db):
            handled = _run(self.sync.sync_reverse_delete("200"))

        self.assertTrue(handled)
        self.assertTrue(self.original_message.deleted)


if __name__ == "__main__":
    unittest.main()
