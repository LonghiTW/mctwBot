"""Bot admin command to broadcast a JSON message to every relay group channel.

Restricted to bot_admins with the ``exclusive_command`` feature (``!announce``
is part of the exclusive_command node). Every usage is written to the backend
log and a DM is sent to bot admins with the ``notifications`` feature.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from app.bot_admins import bot_admin_has_feature
from database import DatabaseManager
from utils.admin_audit import audit_admin_usage
from utils.message_payload import message_from_json


class AnnounceControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        if bot_admin_has_feature(ctx.author.id, "exclusive_command"):
            return True
        await ctx.send("❌ 只有 bot_admins 且啟用 exclusive_command 的管理員才能使用此指令。")
        return False

    @commands.command(name="announce")
    async def announce(self, ctx: commands.Context, group_name: str, *, payload: str):
        data = message_from_json(payload)
        channels = self._group_channel_ids(group_name)
        if not channels:
            await ctx.send(f"Relay group not found or has no channels: `{group_name}`")
            return

        await audit_admin_usage(
            self.bot, ctx, "announce",
            f"group={group_name} channels={len(channels)} payload={payload}",
        )

        sent = 0
        skipped = 0
        failed: list[str] = []
        for channel_id in channels:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(int(channel_id))
                except discord.DiscordException as exc:
                    failed.append(f"{channel_id}: {exc}")
                    continue

            if not isinstance(channel, discord.TextChannel):
                skipped += 1
                continue

            try:
                await channel.send(**data)
                sent += 1
            except discord.DiscordException as exc:
                failed.append(f"{channel_id}: {exc}")

        summary = f"Announcement sent to {sent} channel(s); skipped {skipped} non-text channel(s)."
        if failed:
            details = "\n".join(failed[:5])
            summary += f"\nFailed {len(failed)} channel(s):\n```\n{details}\n```"
        await ctx.send(summary)

    def _group_channel_ids(self, group_name: str) -> list[str]:
        db = DatabaseManager()
        rows = db.fetchall(
            """SELECT lc.channel_id
               FROM linked_channels lc
               JOIN relay_groups rg ON rg.group_id = lc.group_id
               WHERE rg.group_name = ?
               ORDER BY lc.channel_id""",
            (group_name,),
        )
        return [str(row["channel_id"]) for row in rows]


async def setup(bot):
    await bot.add_cog(AnnounceControl(bot))
