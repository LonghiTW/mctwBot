import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from database import DatabaseManager
from app.config_sync import _purge_removed_channel_data
from cogs.relay.thread_sync import ThreadSync

_DAY = 24 * 60 * 60
_KEY = "0123456789abcdef" * 4


class ThreadRetentionTests(unittest.TestCase):
    def setUp(self):
        self.original_instance = DatabaseManager._instance
        DatabaseManager._instance = None
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "threads.db"
        self.env_patch = patch.dict(os.environ, {"DATABASE_ENCRYPTION_KEY": _KEY})
        self.env_patch.start()
        self.db = DatabaseManager(str(self.db_path))
        self.db.execute(
            "INSERT INTO relay_groups (group_id, group_name, owner_guild_id) VALUES (1, 'test', '10')"
        )
        self.db.commit()
        self.source = SimpleNamespace(id=101, send=AsyncMock())
        self.target = SimpleNamespace(id=201, send=AsyncMock())
        self.bot = SimpleNamespace(
            get_channel=lambda channel_id: {101: self.source, 201: self.target}.get(channel_id),
            fetch_channel=AsyncMock(side_effect=lambda channel_id: {101: self.source, 201: self.target}[channel_id]),
        )
        self.sync = ThreadSync(self.bot)
        self.database_patch = patch("cogs.relay.thread_sync.DatabaseManager", return_value=self.db)
        self.database_patch.start()

    def tearDown(self):
        self.database_patch.stop()
        self.db.close()
        self.env_patch.stop()
        DatabaseManager._instance = self.original_instance
        self.temp_dir.cleanup()

    def _add_mapping(self, last_activity_at):
        self.db.execute(
            """INSERT INTO relay_threads
               (group_id, source_thread_id, source_parent_channel_id,
                target_parent_channel_id, target_thread_id, last_activity_at)
               VALUES (1, '101', '11', '21', '201', ?)""",
            (last_activity_at,),
        )
        self.db.commit()

    def test_day_29_notice_is_deduplicated_and_activity_resets_cycle(self):
        now = 2_000_000_000
        self._add_mapping(now - 29 * _DAY)

        expired = asyncio.run(self.sync.maintain_thread_mappings(now))
        self.assertEqual(expired, 0)
        self.source.send.assert_awaited_once()
        self.target.send.assert_awaited_once()

        asyncio.run(self.sync.maintain_thread_mappings(now + 1))
        self.source.send.assert_awaited_once()
        self.target.send.assert_awaited_once()

        self.assertTrue(self.sync.record_user_activity(SimpleNamespace(id=101), now + 2))
        row = self.db.fetchone("SELECT last_activity_at FROM relay_threads")
        self.assertEqual(row["last_activity_at"], now + 2)
        self.assertEqual(self.db.fetchone("SELECT count(*) AS n FROM relay_thread_notices")["n"], 0)

    def test_day_30_removes_only_mapping_without_deleting_discord_threads(self):
        now = 2_000_000_000
        self._add_mapping(now - 30 * _DAY)

        expired = asyncio.run(self.sync.maintain_thread_mappings(now))

        self.assertEqual(expired, 1)
        self.assertIsNone(self.db.fetchone("SELECT 1 FROM relay_threads"))
        self.source.send.assert_not_awaited()
        self.target.send.assert_not_awaited()
        self.assertFalse(hasattr(self.source, "delete"))
        self.assertFalse(hasattr(self.target, "delete"))

    def test_removed_relay_parent_purges_thread_and_message_mappings(self):
        self._add_mapping(2_000_000_000)
        self.db.execute(
            """INSERT INTO relayed_messages
               (original_message_id, original_channel_id, relayed_message_id,
                relayed_channel_id, replied_to_id)
               VALUES ('1001', '101', '2001', '201', NULL)"""
        )
        self.db.commit()

        _purge_removed_channel_data(self.db, {"11"})

        self.assertIsNone(self.db.fetchone("SELECT 1 FROM relay_threads"))
        self.assertEqual(self.db.fetchone("SELECT count(*) AS n FROM relayed_messages")["n"], 0)

    def test_thread_create_is_ignored_for_unconfigured_parent(self):
        thread = SimpleNamespace(
            id=555,
            parent_id=999,
            name="unrelated thread",
            me=None,
            join=AsyncMock(),
        )
        with patch("cogs.relay.thread_sync.log") as mock_log:
            asyncio.run(self.sync.handle_thread_create(thread))

        thread.join.assert_not_awaited()
        mock_log.info.assert_not_called()

    def test_thread_create_joins_configured_parent(self):
        self.db.execute(
            "INSERT INTO linked_channels (channel_id, guild_id, group_id, webhook_url, direction)"
            " VALUES ('21', '10', 1, 'https://example.invalid/hook', 'BOTH')"
        )
        self.db.commit()
        thread = SimpleNamespace(
            id=556,
            parent_id=21,
            name="relay thread",
            me=None,
            join=AsyncMock(),
        )
        with patch.object(self.sync, "mirror_thread_from_relayed_message", new=AsyncMock()):
            asyncio.run(self.sync.handle_thread_create(thread))

        thread.join.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
