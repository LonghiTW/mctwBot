import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import DatabaseManager
from tools.privacy_admin import delete_messages, delete_user, lookup_user

_KEY = "0123456789abcdef" * 4


class PrivacyAdminTests(unittest.TestCase):
    def setUp(self):
        self._original_instance = DatabaseManager._instance
        DatabaseManager._instance = None
        self._temp_dir = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"DATABASE_ENCRYPTION_KEY": _KEY})
        self._env.start()
        self.db = DatabaseManager(str(Path(self._temp_dir.name) / "privacy.db"))
        self.db.execute(
            "INSERT INTO relay_groups (group_id, group_name, owner_guild_id) VALUES (1, 'g', '10')"
        )
        self.db.execute(
            "INSERT INTO group_filters (filter_id, group_id, phrase) VALUES (1, 1, 'bad')"
        )
        self.db.execute(
            "INSERT INTO user_warnings (group_id, user_id, filter_id, warning_count, last_violation_at)"
            " VALUES (1, '42', 1, 2, 100)"
        )
        self.db.execute(
            "INSERT INTO group_blacklist (group_id, blocked_id, type, created_at) VALUES (1, '42', 'USER', 100)"
        )
        self.db.execute(
            "INSERT INTO relay_opt_out (user_id, created_at) VALUES ('42', 100)"
        )
        self.db.execute(
            "INSERT INTO relayed_messages (original_message_id, original_channel_id,"
            " relayed_message_id, relayed_channel_id, replied_to_id)"
            " VALUES ('111', '10', '222', '20', NULL)"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self._env.stop()
        DatabaseManager._instance = self._original_instance
        self._temp_dir.cleanup()

    def test_lookup_returns_user_scoped_rows(self):
        result = lookup_user(self.db, "42")

        self.assertEqual(result["relay_opt_out"][0]["user_id"], "42")
        self.assertEqual(result["user_warnings"][0]["warning_count"], 2)
        self.assertEqual(result["group_blacklist"][0]["blocked_id"], "42")

    def test_delete_user_removes_only_that_user(self):
        self.db.execute(
            "INSERT INTO user_warnings (group_id, user_id, filter_id, warning_count, last_violation_at)"
            " VALUES (1, '99', 1, 1, 100)"
        )
        self.db.commit()

        deleted = delete_user(self.db, "42")

        self.assertEqual(deleted["relay_opt_out"], 1)
        self.assertEqual(deleted["user_warnings"], 1)
        self.assertEqual(deleted["group_blacklist"], 1)
        self.assertIsNone(self.db.fetchone("SELECT 1 FROM relay_opt_out WHERE user_id = '42'"))
        self.assertIsNotNone(self.db.fetchone("SELECT 1 FROM user_warnings WHERE user_id = '99'"))

    def test_delete_messages_removes_mapping_and_keeps_others(self):
        self.db.execute(
            "INSERT INTO relayed_messages (original_message_id, original_channel_id,"
            " relayed_message_id, relayed_channel_id, replied_to_id)"
            " VALUES ('333', '10', '444', '20', NULL)"
        )
        self.db.commit()

        deleted = delete_messages(self.db, ["111"])

        self.assertEqual(deleted["relayed_messages"], 1)
        self.assertIsNone(self.db.fetchone(
            "SELECT 1 FROM relayed_messages WHERE original_message_id = '111'"
        ))
        self.assertIsNotNone(self.db.fetchone(
            "SELECT 1 FROM relayed_messages WHERE original_message_id = '333'"
        ))


if __name__ == "__main__":
    unittest.main()
