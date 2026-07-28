"""Relay phrase filter violation handling and warnings."""
import asyncio
from datetime import datetime

import discord
from discord import Message

from database import DatabaseManager
from utils.admin_notifier import notify_admins


class RelayFilters:
    def __init__(self, bot: discord.Client):
        self.bot = bot

    def track_violation(
        self, db: DatabaseManager, message: Message,
        source: dict, group: dict, filter_row: dict, exec_id: str,
    ) -> None:
        db.execute(
            """INSERT INTO user_warnings (group_id, user_id, filter_id, warning_count, last_violation_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(group_id, user_id, filter_id) DO UPDATE SET
                   warning_count = warning_count + 1,
                   last_violation_at = excluded.last_violation_at""",
            (source["group_id"], str(message.author.id), filter_row["filter_id"], int(datetime.now().timestamp())),
        )
        db.commit()

        stats = db.fetchone(
            "SELECT warning_count FROM user_warnings WHERE group_id = ? AND user_id = ? AND filter_id = ?",
            (source["group_id"], str(message.author.id), filter_row["filter_id"]),
        )
        warning_count = stats["warning_count"] if stats else 0
        threshold = filter_row["threshold"]

        if threshold == 0:
            return
        if threshold == 1:
            db.execute(
                """INSERT OR IGNORE INTO group_blacklist (group_id, blocked_id, type) VALUES (?, ?, 'USER')""",
                (source["group_id"], str(message.author.id)),
            )
            db.commit()
            return
        if warning_count >= threshold:
            db.execute(
                """INSERT OR IGNORE INTO group_blacklist (group_id, blocked_id, type) VALUES (?, ?, 'USER')""",
                (source["group_id"], str(message.author.id)),
            )
            db.commit()
            asyncio.create_task(self.notify_ban(message, group, filter_row, threshold))
            return

        remaining = threshold - warning_count
        asyncio.create_task(self.send_warning(
            message, message.author.id,
            f"⚠️ **Warning:** {filter_row['warning_msg'] or 'Inappropriate language'}\n"
            f"Phrase: ||{filter_row['phrase']}||\nStrikes: {warning_count}/{threshold} ({remaining} left).",
        ))

    async def send_warning(self, destination, user_id: int, text: str) -> None:
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(f"⚠️ **Relay Warning**\nServer: {destination.guild.name}\n{text}")
        except Exception:
            try:
                message = await destination.channel.send(f"<@{user_id}> {text}")
                await asyncio.sleep(15)
                await message.delete()
            except Exception:
                pass

    async def notify_ban(self, message: Message, group: dict, filter_row: dict, threshold: int) -> None:
        await self.send_warning(
            message, message.author.id,
            f"🚫 **You have been blocked from the relay group.**\n"
            f"Reason: Repeated use of prohibited phrase: ||{filter_row['phrase']}||",
        )
        await notify_admins(
            self.bot, "🚫 成員被自動封鎖",
            f"**使用者：** {message.author}（{message.author.id}）\n"
            f"**伺服器：** {message.guild.name}\n"
            f"**群組：** {group['group_name']}\n"
            f"**原因：** 觸發過濾器「{filter_row['phrase']}」達上限（{threshold} 次）",
        )
