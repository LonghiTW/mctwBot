"""SQLite database manager with migration support."""
import os
import re
import threading
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlite3

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
    {
        "version": 3,
        "description": "Emoji cache for cross-server custom emoji relay",
        "sql": """
        CREATE TABLE IF NOT EXISTS relay_emoji_cache (
            source_emoji_id TEXT PRIMARY KEY,
            cache_guild_id TEXT NOT NULL,
            cached_emoji_id TEXT NOT NULL,
            cached_name TEXT NOT NULL,
            animated INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL,
            use_count INTEGER NOT NULL DEFAULT 0
        );
        """,
    },
    {
        "version": 4,
        "description": "Retention, thread activity notices, and relay opt-out",
        "sql": """
        ALTER TABLE relay_threads ADD COLUMN last_activity_at INTEGER NOT NULL DEFAULT 0;
        UPDATE relay_threads
        SET last_activity_at = COALESCE(
            (
                SELECT CAST(((MAX(CAST(rm.original_message_id AS INTEGER)) >> 22) + 1420070400000) / 1000 AS INTEGER)
                FROM relayed_messages rm
                WHERE rm.original_channel_id IN (relay_threads.source_thread_id, relay_threads.target_thread_id)
                   OR rm.relayed_channel_id IN (relay_threads.source_thread_id, relay_threads.target_thread_id)
            ),
            CAST(((CAST(source_thread_id AS INTEGER) >> 22) + 1420070400000) / 1000 AS INTEGER)
        )
        WHERE last_activity_at = 0;
        ALTER TABLE group_blacklist ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0;
        UPDATE group_blacklist
        SET created_at = COALESCE(
            (
                SELECT MAX(last_violation_at) FROM user_warnings
                WHERE user_warnings.group_id = group_blacklist.group_id
                  AND user_warnings.user_id = group_blacklist.blocked_id
            ),
            unixepoch()
        )
        WHERE created_at = 0;
        CREATE TABLE IF NOT EXISTS relay_thread_notices (
            group_id INTEGER NOT NULL,
            thread_id TEXT NOT NULL,
            activity_at INTEGER NOT NULL,
            last_attempt_at INTEGER NOT NULL,
            sent_at INTEGER,
            PRIMARY KEY (group_id, thread_id, activity_at)
        );
        CREATE INDEX IF NOT EXISTS idx_thread_notices_attempt
            ON relay_thread_notices(last_attempt_at);
        CREATE TABLE IF NOT EXISTS relay_opt_out (
            user_id TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL
        );
        """,
    },
    {
        "version": 5,
        "description": "Track relay source author and guild",
        "sql": """
        ALTER TABLE relayed_messages ADD COLUMN original_author_id TEXT;
        ALTER TABLE relayed_messages ADD COLUMN original_guild_id TEXT;
        CREATE INDEX idx_relayed_source_author ON relayed_messages(original_guild_id, original_author_id);
        """,
    },
]

_DATABASE_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _database_key() -> str:
    key = os.getenv("DATABASE_ENCRYPTION_KEY", "").strip()
    if not _DATABASE_KEY_PATTERN.fullmatch(key):
        raise RuntimeError(
            "DATABASE_ENCRYPTION_KEY must be a 64-character hexadecimal key. "
            "Generate one with secrets.token_hex(32) and store it outside the database directory."
        )
    return key.lower()


class DatabaseRow:
    def __init__(self, cursor, values):
        self._values = tuple(values)
        self._column_indexes = {}
        for index, column in enumerate(cursor.description or ()):
            self._column_indexes.setdefault(column[0], index)

    def __getitem__(self, key):
        if isinstance(key, str):
            key = self._column_indexes[key]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._column_indexes)


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
            key = _database_key()
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(f'PRAGMA key = "x\'{key}\'"')
                conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA busy_timeout = 5000")
            except sqlite3.DatabaseError as exc:
                conn.close()
                raise RuntimeError(
                    "Unable to open the encrypted database. Verify DATABASE_ENCRYPTION_KEY; "
                    "existing plaintext databases must be migrated before startup."
                ) from exc
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
        applied = {r[0] for r in conn.execute("SELECT version FROM _migrations").fetchall()}
        for m in MIGRATIONS:
            if m["version"] not in applied:
                print(f"[DB] Migration v{m['version']}: {m['description']}")
                conn.executescript(m["sql"])
                conn.execute("INSERT INTO _migrations (version) VALUES (?)", (m["version"],))
                conn.commit()

    def execute(self, sql: str, params=()):
        return self.conn.execute(sql, params)

    def fetchone(self, sql: str, params=()):
        cursor = self.conn.execute(sql, params)
        row = cursor.fetchone()
        return DatabaseRow(cursor, row) if row is not None else None

    def fetchall(self, sql: str, params=()):
        cursor = self.conn.execute(sql, params)
        return [DatabaseRow(cursor, row) for row in cursor.fetchall()]

    def commit(self):
        self.conn.commit()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
