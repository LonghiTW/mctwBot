"""Repository helpers for relayed message mappings."""
from database import DatabaseManager


class RelayMessageStore:
    def __init__(self, db: DatabaseManager | None = None):
        self.db = db or DatabaseManager()

    def has_original(self, original_message_id: str) -> bool:
        return bool(self.db.fetchone(
            "SELECT 1 FROM relayed_messages WHERE original_message_id = ? LIMIT 1",
            (original_message_id,),
        ))

    def original_for_relayed(self, relayed_message_id: str):
        return self.db.fetchone(
            """SELECT original_message_id, original_channel_id
               FROM relayed_messages WHERE relayed_message_id = ?""",
            (relayed_message_id,),
        )

    def relayed_for_original(self, original_message_id: str) -> list[dict]:
        return self.db.fetchall(
            "SELECT relayed_message_id, relayed_channel_id FROM relayed_messages WHERE original_message_id = ?",
            (original_message_id,),
        )

    def replies_to_original(self, original_message_id: str) -> list[dict]:
        return self.db.fetchall(
            """SELECT relayed_message_id, relayed_channel_id
               FROM relayed_messages
               WHERE replied_to_id = ?""",
            (original_message_id,),
        )

    def delete_mapping(self, original_message_id: str, relayed_message_id: str) -> None:
        self.db.execute(
            """DELETE FROM relayed_messages
               WHERE original_message_id = ? AND relayed_message_id = ?""",
            (original_message_id, relayed_message_id),
        )
        self.db.commit()
