import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.relay.message_sync import MessageSync
from cogs.relay.queue import RelayQueue


def not_found(code):
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(response, {"code": code, "message": "Not Found"})


class BanCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_unknown_message_triggers_cleanup_and_rechecks(self):
        rows = [{"original_message_id": str(i), "original_channel_id": str(i + 10)} for i in range(1, 5)]
        db = SimpleNamespace(fetchall=Mock(return_value=rows))
        channels = {
            11: SimpleNamespace(fetch_message=AsyncMock(side_effect=not_found(10008))),
            12: SimpleNamespace(fetch_message=AsyncMock(side_effect=not_found(10003))),
            13: SimpleNamespace(fetch_message=AsyncMock(side_effect=[object(), not_found(10008)])),
        }
        async def fetch_channel(channel_id):
            if channel_id == 14:
                raise not_found(10008)
            return channels[channel_id]
        sync = MessageSync(SimpleNamespace(fetch_channel=fetch_channel))
        sync.sync_forward_delete = AsyncMock(return_value=True)
        with patch("cogs.relay.message_sync.DatabaseManager", return_value=db), \
             patch("cogs.relay.message_sync.asyncio.sleep", new=AsyncMock()), \
             patch("cogs.relay.message_sync.relay_queue.pending_for_author", return_value=[]):
            await sync.check_banned_user("7", "8")
        self.assertEqual(sync.sync_forward_delete.await_args_list, [unittest.mock.call("1", "11", ban_cleanup=True), unittest.mock.call("3", "13", ban_cleanup=True)])
        self.assertTrue(all(call.args[1] == ("7", "8") for call in db.fetchall.call_args_list))

    async def test_pending_message_is_checked_and_duplicate_bans_deduplicated(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def sleep(_):
            entered.set()
            await release.wait()
        db = SimpleNamespace(fetchall=Mock(return_value=[]))
        channel = SimpleNamespace(fetch_message=AsyncMock(side_effect=not_found(10008)))
        sync = MessageSync(SimpleNamespace(fetch_channel=AsyncMock(return_value=channel)))
        sync.sync_forward_delete = AsyncMock(return_value=True)
        pending = [{"original_msg_id": "1", "original_channel_id": "2"}]
        with patch("cogs.relay.message_sync.DatabaseManager", return_value=db), \
             patch("cogs.relay.message_sync.asyncio.sleep", side_effect=sleep), \
             patch("cogs.relay.message_sync.relay_queue.pending_for_author", return_value=pending):
            task = asyncio.create_task(sync.check_banned_user("7", "8"))
            await entered.wait()
            await sync.check_banned_user("7", "8")
            release.set()
            await task
        sync.sync_forward_delete.assert_awaited_once_with("1", "2", ban_cleanup=True)

    async def test_forward_permission_prevents_cancellation(self):
        db = SimpleNamespace(fetchone=Mock(return_value={"allow_forward_delete": 0}))
        sync = MessageSync(SimpleNamespace())
        sync.mark_replied_message_deleted = AsyncMock()
        with patch("cogs.relay.message_sync.DatabaseManager", return_value=db), \
             patch("cogs.relay.message_sync.configured_channel_id_for_stored_channel", return_value="2"), \
             patch("cogs.relay.message_sync.relay_queue.cancel") as cancel:
            self.assertTrue(await sync.sync_forward_delete("1", "2", ban_cleanup=True))
            cancel.assert_not_called()
            self.assertFalse(await sync.sync_forward_delete("3", "2"))
            cancel.assert_called_once_with("3")

    async def test_cleanup_retries_partial_failure_and_exception_despite_guard(self):
        db = SimpleNamespace(fetchall=Mock(return_value=[{"original_message_id": "1", "original_channel_id": "2"}]),
                             fetchone=Mock(return_value={"allow_forward_delete": 1}),
                             execute=Mock(), commit=Mock())
        channel = SimpleNamespace(fetch_message=AsyncMock(side_effect=not_found(10008)))
        sync = MessageSync(SimpleNamespace(fetch_channel=AsyncMock(return_value=channel)))
        sync.mark_replied_message_deleted = AsyncMock(side_effect=[RuntimeError("temporary"), None, None])
        store = SimpleNamespace(relayed_for_original=Mock(return_value=[{"relayed_message_id": "4", "relayed_channel_id": "5"}]),
                                delete_mapping=Mock())
        sync.webhooks.delete_message = AsyncMock(side_effect=[RuntimeError("temporary"), None])
        with patch("cogs.relay.message_sync.DatabaseManager", return_value=db), \
             patch("cogs.relay.message_sync.RelayMessageStore", return_value=store), \
             patch("cogs.relay.message_sync.configured_channel_id_for_stored_channel", return_value="2"), \
             patch("cogs.relay.message_sync.asyncio.sleep", new=AsyncMock()), \
             patch("cogs.relay.message_sync.relay_queue.pending_for_author", return_value=[]):
            await sync.check_banned_user("7", "8")
        self.assertEqual(sync.mark_replied_message_deleted.await_count, 3)
        self.assertEqual(sync.webhooks.delete_message.await_count, 2)
        store.delete_mapping.assert_called_once_with("1", "4")

    async def test_database_errors_do_not_escape_event(self):
        sync = MessageSync(SimpleNamespace())
        with patch("cogs.relay.message_sync.DatabaseManager", side_effect=RuntimeError("db")), \
             patch("cogs.relay.message_sync.asyncio.sleep", new=AsyncMock()):
            await sync.check_banned_user("7", "8")
        self.assertFalse(sync._ban_checks)

    async def test_queue_pending_metadata_and_cancel_expiry(self):
        queue = RelayQueue()
        meta = {"original_msg_id": "1", "original_guild_id": "7", "original_author_id": "8"}
        await queue.add("url", {}, meta)
        self.assertEqual(queue.pending_for_author("7", "8"), [meta])
        self.assertEqual(queue.pending_for_author("9", "8"), [])
        queue.cancel("1")
        queue._cancelled["1"] = 0
        self.assertTrue(queue._is_cancelled("1"))
