import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlcipher3 import dbapi2 as sqlcipher

from database import DatabaseManager
from database.database import MIGRATIONS, _database_key
from database.encryption_migration import encrypt_database


class DatabaseEncryptionTests(unittest.TestCase):
    def test_plaintext_database_is_rejected_by_encrypted_manager(self):
        key = "0123456789abcdef" * 4
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"DATABASE_ENCRYPTION_KEY": key}
        ):
            path = Path(temp_dir) / "plaintext.db"
            plaintext = sqlite3.connect(path)
            try:
                plaintext.execute("CREATE TABLE sample (value TEXT)")
                plaintext.commit()
            finally:
                plaintext.close()

            previous_instance = DatabaseManager._instance
            DatabaseManager._instance = None
            try:
                with self.assertRaisesRegex(RuntimeError, "existing plaintext databases must be migrated"):
                    DatabaseManager(str(path))
            finally:
                DatabaseManager._instance = previous_instance

    def test_existing_v3_database_migrates_and_backfills_retention_times(self):
        key = "0123456789abcdef" * 4
        now = 2_000_000_000
        last_activity_at = now - 2 * 86400
        last_violation_at = now - 20 * 86400
        snowflake = ((last_activity_at * 1000 - 1420070400000) << 22)

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"DATABASE_ENCRYPTION_KEY": key}
        ):
            plaintext_path = Path(temp_dir) / "v3.db"
            encrypted_path = Path(temp_dir) / "v4.db"
            plaintext = sqlite3.connect(plaintext_path)
            try:
                plaintext.execute(
                    "CREATE TABLE _migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
                )
                for migration in MIGRATIONS[:3]:
                    plaintext.executescript(migration["sql"])
                    plaintext.execute(
                        "INSERT INTO _migrations (version) VALUES (?)",
                        (migration["version"],),
                    )
                plaintext.execute(
                    "INSERT INTO relay_groups (group_id, group_name, owner_guild_id) VALUES (1, 'test', '10')"
                )
                plaintext.execute(
                    "INSERT INTO group_filters (filter_id, group_id, phrase) VALUES (1, 1, 'blocked')"
                )
                plaintext.execute(
                    """INSERT INTO user_warnings
                       (group_id, user_id, filter_id, warning_count, last_violation_at)
                       VALUES (1, '42', 1, 1, ?)""",
                    (last_violation_at,),
                )
                plaintext.execute(
                    "INSERT INTO group_blacklist (group_id, blocked_id, type) VALUES (1, '42', 'USER')"
                )
                plaintext.execute(
                    """INSERT INTO relay_threads
                       (group_id, source_thread_id, source_parent_channel_id,
                        target_parent_channel_id, target_thread_id)
                       VALUES (1, '101', '11', '21', '201')"""
                )
                plaintext.execute(
                    """INSERT INTO relayed_messages
                       (original_message_id, original_channel_id, relayed_message_id,
                        relayed_channel_id, replied_to_id)
                       VALUES (?, '101', ?, '201', NULL)""",
                    (str(snowflake), str(snowflake + 1)),
                )
                plaintext.commit()
            finally:
                plaintext.close()

            encrypt_database(plaintext_path, encrypted_path, key)
            original_instance = DatabaseManager._instance
            DatabaseManager._instance = None
            try:
                db = DatabaseManager(str(encrypted_path))
                thread_row = db.fetchone("SELECT last_activity_at FROM relay_threads")
                blacklist_row = db.fetchone("SELECT created_at FROM group_blacklist")
                self.assertEqual(thread_row["last_activity_at"], last_activity_at)
                self.assertEqual(blacklist_row["created_at"], last_violation_at)
                db.close()
            finally:
                DatabaseManager._instance = original_instance

    def test_plaintext_database_is_copied_to_encrypted_database(self):
        key = "0123456789abcdef" * 4
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "plain.db"
            destination = Path(temp_dir) / "encrypted.db"
            plaintext = sqlite3.connect(source)
            try:
                plaintext.execute("CREATE TABLE sample (value TEXT NOT NULL)")
                plaintext.execute("INSERT INTO sample VALUES ('preserved')")
                plaintext.commit()
            finally:
                plaintext.close()

            encrypt_database(source, destination, key)

            encrypted = sqlcipher.connect(destination)
            try:
                encrypted.execute(f'PRAGMA key = "x\'{key}\'"')
                self.assertEqual(encrypted.execute("SELECT value FROM sample").fetchone()[0], "preserved")
            finally:
                encrypted.close()
            self.assertTrue(source.exists())
            plain = sqlite3.connect(destination)
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    plain.execute("SELECT name FROM sqlite_master").fetchall()
            finally:
                plain.close()

    def test_database_key_is_required_and_validated(self):
        with patch.dict(os.environ, {"DATABASE_ENCRYPTION_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "64-character hexadecimal"):
                _database_key()

    def test_database_file_requires_sqlcipher(self):
        key = "0123456789abcdef" * 4
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"DATABASE_ENCRYPTION_KEY": key}
        ):
            path = Path(temp_dir) / "encrypted.db"
            db = DatabaseManager(str(path))
            row = db.fetchone("SELECT count(*) AS table_count FROM sqlite_master")
            self.assertGreater(row["table_count"], 0)
            self.assertEqual(row[0], row["table_count"])
            db.close()

            self.assertFalse(path.read_bytes().startswith(b"SQLite format 3"))
            plain_connection = sqlite3.connect(path)
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    plain_connection.execute("SELECT name FROM sqlite_master").fetchall()
            finally:
                plain_connection.close()


if __name__ == "__main__":
    unittest.main()
