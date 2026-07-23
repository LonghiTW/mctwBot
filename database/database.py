"""SQLite database manager with migration support."""
import os
import sqlite3
import threading
from pathlib import Path

from app.config import DATABASE_PATH

MIGRATIONS = [
    {
        "version": 1,
        "description": "Initial schema",
        "sql": """
        CREATE TABLE IF NOT EXISTS relay_groups (
            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL UNIQUE,
            owner_guild_id TEXT NOT NULL,
            owner_user_id TEXT
        );
        CREATE TABLE IF NOT EXISTS linked_channels (
            channel_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            group_id INTEGER NOT NULL,
            webhook_url TEXT NOT NULL,
            direction TEXT DEFAULT 'BOTH' NOT NULL,
            brand_name TEXT,
            allow_forward_delete INTEGER DEFAULT 1 NOT NULL,
            allow_reverse_delete INTEGER DEFAULT 0 NOT NULL,
            delete_delay_hours INTEGER DEFAULT 0 NOT NULL,
            process_bot_messages INTEGER DEFAULT 0 NOT NULL,
            allow_auto_role_creation INTEGER DEFAULT 0 NOT NULL,
            FOREIGN KEY (group_id) REFERENCES relay_groups(group_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS role_mappings (
            mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            role_name TEXT NOT NULL,
            role_id TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES relay_groups(group_id) ON DELETE CASCADE,
            UNIQUE(group_id, guild_id, role_name)
        );
        CREATE TABLE IF NOT EXISTS relayed_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_message_id TEXT NOT NULL,
            original_channel_id TEXT NOT NULL,
            relayed_message_id TEXT NOT NULL,
            relayed_channel_id TEXT NOT NULL,
            replied_to_id TEXT
        );
        CREATE TABLE IF NOT EXISTS group_blacklist (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            blocked_id TEXT NOT NULL,
            type TEXT DEFAULT 'USER',
            FOREIGN KEY (group_id) REFERENCES relay_groups(group_id) ON DELETE CASCADE,
            UNIQUE(group_id, blocked_id)
        );
        CREATE TABLE IF NOT EXISTS group_filters (
            filter_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            phrase TEXT NOT NULL,
            threshold INTEGER DEFAULT 3,
            warning_msg TEXT,
            FOREIGN KEY (group_id) REFERENCES relay_groups(group_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS user_warnings (
            warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            filter_id INTEGER NOT NULL,
            warning_count INTEGER DEFAULT 0,
            last_violation_at INTEGER,
            FOREIGN KEY (group_id) REFERENCES relay_groups(group_id) ON DELETE CASCADE,
            FOREIGN KEY (filter_id) REFERENCES group_filters(filter_id) ON DELETE CASCADE,
            UNIQUE(group_id, user_id, filter_id)
        );
        CREATE INDEX IF NOT EXISTS idx_linked_channels_group ON linked_channels(group_id);
        CREATE INDEX IF NOT EXISTS idx_relayed_original ON relayed_messages(original_message_id);
        CREATE INDEX IF NOT EXISTS idx_relayed_relayed ON relayed_messages(relayed_message_id);
        CREATE INDEX IF NOT EXISTS idx_blacklist_group ON group_blacklist(group_id);
        """,
    },
    {
        "version": 2,
        "description": "Track relayed thread/forum post mappings",
        "sql": """
        CREATE TABLE IF NOT EXISTS relay_threads (
            mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            source_thread_id TEXT NOT NULL,
            source_parent_channel_id TEXT NOT NULL,
            target_parent_channel_id TEXT NOT NULL,
            target_thread_id TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES relay_groups(group_id) ON DELETE CASCADE,
            UNIQUE(group_id, source_thread_id, target_parent_channel_id)
        );
        CREATE INDEX IF NOT EXISTS idx_relay_threads_source
            ON relay_threads(group_id, source_thread_id);
        CREATE INDEX IF NOT EXISTS idx_relay_threads_target
            ON relay_threads(group_id, target_thread_id);
        """,
    },
]


class DatabaseManager:
    """Thread-safe SQLite singleton with auto-migration."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self, db_path: str | None = None):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True

        if db_path is None:
            db_path = DATABASE_PATH

        db_file = Path(db_path)
        if not db_file.is_absolute():
            db_file = Path.cwd() / db_file
        db_file.parent.mkdir(parents=True, exist_ok=True)

        self._db_path = str(db_file)
        self._local = threading.local()
        self._run_migrations()

    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _run_migrations(self):
        conn = self.conn
        conn.execute(
            """CREATE TABLE IF NOT EXISTS _migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        applied = {r["version"] for r in conn.execute("SELECT version FROM _migrations").fetchall()}
        for m in MIGRATIONS:
            if m["version"] not in applied:
                print(f"[DB] Migration v{m['version']}: {m['description']}")
                conn.executescript(m["sql"])
                conn.execute("INSERT INTO _migrations (version) VALUES (?)", (m["version"],))
                conn.commit()

    def execute(self, sql: str, params=()):
        return self.conn.execute(sql, params)

    def fetchone(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchall()

    def commit(self):
        self.conn.commit()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
