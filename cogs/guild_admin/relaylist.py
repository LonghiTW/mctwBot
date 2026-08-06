"""!relaylist — view relay groups for Discord guild admins.

Permission is intentionally independent from ``bot_admins``: only members
with ``manage_guild`` or ``administrator`` in a guild may view the relay
list, even if they are listed in ``bot_admins``.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from database import DatabaseManager
from app.config_sync import load_config
from cogs.relay.admin_views import format_channel_link


class Relaylist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def can_view_relaylist(self, author: discord.User | discord.Member | None) -> bool:
        if author is None:
            return False
        if isinstance(author, discord.Member) and author.guild:
            return author.guild_permissions.manage_guild or author.guild_permissions.administrator
        return False

    @commands.command(name="relaylist")
    async def list_relays(self, ctx: commands.Context):
        """列出所有中繼群組與所屬頻道／伺服器。"""
        if not self.can_view_relaylist(ctx.author):
            await ctx.send("❌ 你沒有權限檢視中繼列表。僅限擁有「管理伺服器」權限者使用。")
            return

        chunks = self.relaylist_chunks()
        if not chunks:
            await ctx.send("目前沒有設定任何中繼群組。")
            return

        for chunk in chunks:
            await ctx.send(chunk)

    def relaylist_chunks(self) -> list[str]:
        hidden_groups = self._hidden_groups()

        db = DatabaseManager()
        groups = db.fetchall("SELECT * FROM relay_groups ORDER BY group_name")
        if not groups:
            return []

        lines: list[str] = []
        for group in groups:
            if group["group_name"] in hidden_groups:
                continue

            channels = db.fetchall(
                "SELECT * FROM linked_channels WHERE group_id = ? ORDER BY guild_id, channel_id",
                (group["group_id"],),
            )
            lines.append(f"**{group['group_name']}**")

            if not channels:
                lines.append("  └ *無頻道*")
                continue

            for index, channel in enumerate(channels):
                prefix = "  └" if index == len(channels) - 1 else "  ├"
                direction = "🔄" if channel["direction"] == "BOTH" else ("📤" if channel["direction"] == "SEND_ONLY" else "📥")
                lines.append(f"{prefix} {direction} {format_channel_link(channel['guild_id'], channel['channel_id'])}")

            lines.append("")

        text = "\n".join(lines).strip()
        if not text:
            return ["目前沒有顯示任何中繼群組。"]
        return self._chunk_text(text)

    def _hidden_groups(self) -> set[str]:
        config = load_config()
        relay_cfg = config.get("relay", {})
        hidden_groups: set[str] = set()
        for group_config in relay_cfg.get("groups", []):
            if group_config.get("hidden", False):
                hidden_groups.add(str(group_config.get("name", "")).strip())
        return hidden_groups

    def _chunk_text(self, text: str) -> list[str]:
        if len(text) <= 1900:
            return [text]

        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks


async def setup(bot: commands.Bot):
    await bot.add_cog(Relaylist(bot))
