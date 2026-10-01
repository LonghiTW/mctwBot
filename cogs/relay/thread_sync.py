"""Thread/forum lifecycle synchronization for relay."""
from datetime import datetime, timezone

import discord

from database import DatabaseManager
from utils.log_manager import LogManager
from .routing import configured_channel_id_for_stored_channel

log = LogManager
_DAY_SECONDS = 24 * 60 * 60
_THREAD_WARNING_AFTER = 29 * _DAY_SECONDS
_THREAD_MAPPING_TTL = 30 * _DAY_SECONDS
_NOTICE_RETRY_SECONDS = 6 * 60 * 60


class ThreadSync:
    def __init__(self, bot: discord.Client):
        self.bot = bot

    def record_user_activity(self, thread: discord.Thread, activity_at: int | None = None) -> bool:
        db = DatabaseManager()
        thread_id = str(thread.id)
        activity_at = activity_at or int(datetime.now(timezone.utc).timestamp())
        mappings = db.fetchall(
            """SELECT group_id, source_thread_id, target_thread_id
               FROM relay_threads
               WHERE source_thread_id = ? OR target_thread_id = ?""",
            (thread_id, thread_id),
        )
        if not mappings:
            return False

        db.execute(
            """UPDATE relay_threads SET last_activity_at = ?
               WHERE (source_thread_id = ? OR target_thread_id = ?)
               AND last_activity_at < ?""",
            (activity_at, thread_id, thread_id, activity_at),
        )
        for mapping in mappings:
            db.execute(
                """DELETE FROM relay_thread_notices
                   WHERE group_id = ? AND activity_at < ?
                   AND thread_id IN (?, ?)""",
                (
                    mapping["group_id"], activity_at,
                    mapping["source_thread_id"], mapping["target_thread_id"],
                ),
            )
        db.commit()
        return True

    async def maintain_thread_mappings(self, now: int | None = None) -> int:
        now = now or int(datetime.now(timezone.utc).timestamp())
        warning_cutoff = now - _THREAD_WARNING_AFTER
        expiry_cutoff = now - _THREAD_MAPPING_TTL
        db = DatabaseManager()
        due = db.fetchall(
            """SELECT group_id, source_thread_id, target_thread_id, last_activity_at
               FROM relay_threads
               WHERE last_activity_at <= ? AND last_activity_at > ?""",
            (warning_cutoff, expiry_cutoff),
        )

        for mapping in due:
            for thread_id in {mapping["source_thread_id"], mapping["target_thread_id"]}:
                await self._send_expiry_notice(
                    db, int(mapping["group_id"]), str(thread_id),
                    int(mapping["last_activity_at"]), now,
                )

        expired = db.execute(
            "DELETE FROM relay_threads WHERE last_activity_at <= ?",
            (expiry_cutoff,),
        ).rowcount
        db.execute(
            "DELETE FROM relay_thread_notices WHERE activity_at <= ?",
            (expiry_cutoff,),
        )
        db.commit()
        if expired:
            log.info("THREAD-PRUNE", f"Removed {expired} inactive thread mapping(s).")
        return expired

    async def _send_expiry_notice(
        self, db: DatabaseManager, group_id: int, thread_id: str,
        activity_at: int, now: int,
    ) -> None:
        notice = db.fetchone(
            """SELECT last_attempt_at, sent_at FROM relay_thread_notices
               WHERE group_id = ? AND thread_id = ? AND activity_at = ?""",
            (group_id, thread_id, activity_at),
        )
        if notice and notice["sent_at"] is not None:
            return
        if notice and now - int(notice["last_attempt_at"]) < _NOTICE_RETRY_SECONDS:
            return

        db.execute(
            """INSERT INTO relay_thread_notices
               (group_id, thread_id, activity_at, last_attempt_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(group_id, thread_id, activity_at)
               DO UPDATE SET last_attempt_at = excluded.last_attempt_at""",
            (group_id, thread_id, activity_at, now),
        )
        db.commit()

        try:
            thread = self.bot.get_channel(int(thread_id))
            if thread is None:
                thread = await self.bot.fetch_channel(int(thread_id))
            deadline = datetime.fromtimestamp(
                activity_at + _THREAD_MAPPING_TTL, tz=timezone.utc,
            ).strftime("%Y-%m-%d %H:%M UTC")
            await thread.send(
                "⚠️ 此討論串已接近 30 天無使用者活動。若期限前沒有新訊息，"
                f"Bot 將於 {deadline} 解除同步關聯；不會刪除討論串或訊息，"
                "但之後的新訊息可能建立新的鏡像討論串，編輯/刪除同步也將停止。"
                "來源串或鏡像串中的新使用者訊息會重新起算。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            log.warn("THREAD-EXPIRY-NOTICE", f"Could not notify thread {thread_id}: {exc}")
            return

        db.execute(
            """UPDATE relay_thread_notices SET sent_at = ?
               WHERE group_id = ? AND thread_id = ? AND activity_at = ?""",
            (now, group_id, thread_id, activity_at),
        )
        db.commit()

    async def handle_thread_create(self, thread: discord.Thread) -> None:
        if not self._is_configured_parent(thread):
            return

        try:
            if thread.me is None:
                await thread.join()
                log.info("THREAD", f"Joined configured thread {thread.id} in parent {thread.parent_id}")
        except Exception as exc:
            log.warn("THREAD", f"Failed to join thread {thread.id}: {exc}")

        await self.mirror_thread_from_relayed_message(thread)

    @staticmethod
    def _is_configured_parent(thread: discord.Thread) -> bool:
        parent_id = getattr(thread, "parent_id", None)
        if parent_id is None:
            return False
        db = DatabaseManager()
        return bool(db.fetchone(
            "SELECT 1 FROM linked_channels WHERE channel_id = ?",
            (str(parent_id),),
        ))

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
                     target_parent_channel_id, target_thread_id, last_activity_at)
                    VALUES (?, ?, ?, ?, ?, unixepoch())""",
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
