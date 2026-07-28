"""Thread/forum lifecycle synchronization for relay."""
import discord

from database import DatabaseManager
from utils.log_manager import LogManager
from .routing import configured_channel_id_for_stored_channel

log = LogManager


class ThreadSync:
    def __init__(self, bot: discord.Client):
        self.bot = bot

    async def handle_thread_create(self, thread: discord.Thread) -> None:
        try:
            if thread.me is None:
                await thread.join()
                log.info("THREAD", f"Joined new thread {thread.id} ({thread.name})")
        except Exception as exc:
            log.warn("THREAD", f"Failed to join thread {thread.id}: {exc}")

        await self.mirror_thread_from_relayed_message(thread)

    async def handle_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        if (before.locked == after.locked
                and before.archived == after.archived
                and before.name == after.name):
            return

        db = DatabaseManager()

        if db.fetchone("SELECT 1 FROM relay_threads WHERE target_thread_id = ? LIMIT 1", (str(after.id),)):
            return

        mappings = db.fetchall(
            "SELECT * FROM relay_threads WHERE source_thread_id = ?", (str(after.id),)
        )
        if not mappings:
            return

        kwargs = {}
        if before.locked != after.locked:
            kwargs["locked"] = after.locked
        if before.archived != after.archived:
            kwargs["archived"] = after.archived
        if before.name != after.name:
            kwargs["name"] = after.name

        for mapping in mappings:
            try:
                target = self.bot.get_channel(int(mapping["target_thread_id"]))
                if target is None:
                    target = await self.bot.fetch_channel(int(mapping["target_thread_id"]))
                await target.edit(**kwargs)
            except discord.NotFound:
                db.execute("DELETE FROM relay_threads WHERE target_thread_id = ?", (mapping["target_thread_id"],))
                db.commit()
            except Exception as exc:
                log.error("THR-UPD", f"Failed {mapping['target_thread_id']}: {exc}")

    async def handle_thread_delete(self, thread: discord.Thread) -> None:
        db = DatabaseManager()
        mappings = db.fetchall(
            "SELECT * FROM relay_threads WHERE source_thread_id = ?", (str(thread.id),)
        )
        if not mappings:
            return

        for mapping in mappings:
            try:
                target = self.bot.get_channel(int(mapping["target_thread_id"]))
                if target is None:
                    target = await self.bot.fetch_channel(int(mapping["target_thread_id"]))
                if target:
                    await target.delete()
            except discord.NotFound:
                pass
            except Exception as exc:
                log.error("THR-DEL", f"Failed {mapping['target_thread_id']}: {exc}")

        db.execute(
            "DELETE FROM relay_threads WHERE source_thread_id = ?", (str(thread.id),)
        )
        db.commit()

    async def mirror_thread_from_relayed_message(self, thread: discord.Thread) -> bool:
        db = DatabaseManager()
        link = db.fetchone(
            """SELECT original_message_id, original_channel_id
               FROM relayed_messages
               WHERE relayed_message_id = ? LIMIT 1""",
            (str(thread.id),),
        )
        if not link:
            return False

        original_cfg_id = configured_channel_id_for_stored_channel(db, link["original_channel_id"])
        source = db.fetchone(
            "SELECT group_id FROM linked_channels WHERE channel_id = ?",
            (original_cfg_id,),
        )
        if not source:
            return True

        existing = db.fetchone(
            """SELECT 1 FROM relay_threads
               WHERE group_id = ? AND target_thread_id = ? LIMIT 1""",
            (source["group_id"], str(thread.id)),
        )
        if existing:
            return True

        try:
            original_channel = await self.bot.fetch_channel(int(link["original_channel_id"]))
            original_message = await original_channel.fetch_message(int(link["original_message_id"]))
            mirrored = await original_message.create_thread(
                name=thread.name[:100] or "Relayed thread",
                auto_archive_duration=thread.auto_archive_duration,
                slowmode_delay=thread.slowmode_delay,
                reason="Relay thread opened from mirrored message",
            )
            try:
                if mirrored.me is None:
                    await mirrored.join()
            except Exception:
                pass
        except discord.HTTPException as exc:
            log.warn("THREAD-MIRROR", f"Failed to mirror starter thread {thread.id}: {exc}")
            return True

        db.execute(
            "DELETE FROM relay_threads WHERE group_id = ? AND target_thread_id = ?",
            (source["group_id"], str(thread.id)),
        )
        db.execute(
            """INSERT OR REPLACE INTO relay_threads
               (group_id, source_thread_id, source_parent_channel_id,
                target_parent_channel_id, target_thread_id)
               VALUES (?, ?, ?, ?, ?)""",
            (
                source["group_id"],
                str(mirrored.id),
                str(mirrored.parent_id),
                str(thread.parent_id),
                str(thread.id),
            ),
        )
        db.commit()
        log.info("THREAD-MIRROR", f"Mapped original starter thread {mirrored.id} -> relayed starter thread {thread.id}")
        return True
