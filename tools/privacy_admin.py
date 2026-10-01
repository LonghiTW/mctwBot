"""Operator CLI for handling privacy access and deletion requests.

Run this with the same ``DATABASE_ENCRYPTION_KEY`` and ``DATABASE_PATH`` that the
bot uses, while the bot is stopped, so requests are handled against the live
encrypted database.

Examples:
    python -m tools.privacy_admin lookup --user-id 123456789012345678
    python -m tools.privacy_admin delete --user-id 123456789012345678 --confirm
    python -m tools.privacy_admin delete-messages --message-id 987654321098765432 --confirm
"""
from __future__ import annotations

import argparse
import json

from database import DatabaseManager
from utils.log_manager import LogManager

log = LogManager

# Tables that store a Discord user id and can be deleted per user.
_USER_TABLES = {
    "relay_opt_out": "user_id",
    "user_warnings": "user_id",
    "group_blacklist": "blocked_id",
}


def lookup_user(db: DatabaseManager, user_id: str) -> dict:
    """Return every stored row that references ``user_id``."""
    result: dict[str, list] = {}
    for table, column in _USER_TABLES.items():
        rows = db.fetchall(
            f"SELECT * FROM {table} WHERE {column} = ?",
            (user_id,),
        )
        result[table] = [dict(row) for row in rows]
    return result


def delete_user(db: DatabaseManager, user_id: str) -> dict:
    """Delete every user-scoped row for ``user_id`` and return the counts.

    Message and thread mappings store Discord snowflake ids, not user ids, so
    they cannot be selected by user here; they are removed automatically by the
    fixed 30-day retention policy.
    """
    deleted: dict[str, int] = {}
    for table, column in _USER_TABLES.items():
        cursor = db.execute(f"DELETE FROM {table} WHERE {column} = ?", (user_id,))
        deleted[table] = cursor.rowcount
    db.commit()
    return deleted


def delete_messages(db: DatabaseManager, message_ids: list[str]) -> dict:
    """Delete relay/thread mappings for specific message ids."""
    ids = [str(message_id) for message_id in message_ids]
    placeholders = ",".join("?" for _ in ids)
    deleted: dict[str, int] = {}

    deleted["relay_thread_notices"] = db.execute(
        f"DELETE FROM relay_thread_notices WHERE thread_id IN ({placeholders})",
        tuple(ids),
    ).rowcount
    deleted["relayed_messages"] = db.execute(
        f"""DELETE FROM relayed_messages
            WHERE original_message_id IN ({placeholders})
               OR relayed_message_id IN ({placeholders})""",
        tuple(ids) + tuple(ids),
    ).rowcount
    deleted["relay_threads"] = db.execute(
        f"""DELETE FROM relay_threads
            WHERE source_thread_id IN ({placeholders})
               OR target_thread_id IN ({placeholders})""",
        tuple(ids) + tuple(ids),
    ).rowcount
    db.commit()
    return deleted


def _dump(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Privacy access/deletion helper for MCTW Bot.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lookup = subparsers.add_parser("lookup", help="Show stored rows for a user id")
    lookup.add_argument("--user-id", required=True)

    delete = subparsers.add_parser("delete", help="Delete user-scoped rows for a user id")
    delete.add_argument("--user-id", required=True)
    delete.add_argument("--confirm", action="store_true", help="Required to actually delete")

    delete_messages = subparsers.add_parser("delete-messages", help="Delete mappings for message ids")
    delete_messages.add_argument("--message-id", action="append", required=True)
    delete_messages.add_argument("--confirm", action="store_true", help="Required to actually delete")

    args = parser.parse_args()
    db = DatabaseManager()
    try:
        if args.command == "lookup":
            _dump(lookup_user(db, str(args.user_id)))
        elif args.command == "delete":
            if not args.confirm:
                parser.error("Refusing to delete without --confirm.")
            deleted = delete_user(db, str(args.user_id))
            log.info("PRIVACY-DELETE", f"user_id={args.user_id} deleted={deleted}")
            _dump(deleted)
        elif args.command == "delete-messages":
            if not args.confirm:
                parser.error("Refusing to delete without --confirm.")
            deleted = delete_messages(db, [str(value) for value in args.message_id])
            log.info("PRIVACY-DELETE", f"message_ids={args.message_id} deleted={deleted}")
            _dump(deleted)
    finally:
        db.close()


if __name__ == "__main__":
    main()
