"""Guild tracker — detects when the bot is removed/kicked from a guild.

Triggers an instant DM notification to admin_user_ids if the lost guild
contained any relay-linked channels.
"""
import discord
from discord.ext import commands

from database import DatabaseManager
from utils.log_manager import LogManager
from utils.admin_notifier import notify_admins

log = LogManager


class GuildTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Fires when the bot leaves a guild (kicked, banned, or guild deleted)."""
        log.info("GUILD", f"Left guild: {guild.name} (ID: {guild.id})")

        db = DatabaseManager()

        # Find any relay-linked channels in this guild
        rows = db.fetchall(
            """SELECT lc.channel_id, lc.group_id, rg.group_name
               FROM linked_channels lc
               JOIN relay_groups rg ON rg.group_id = lc.group_id
               WHERE lc.guild_id = ?""",
            (str(guild.id),),
        )

        if not rows:
            log.info("GUILD", f"No relay channels found in guild {guild.id}, skipping notification.")
            return

        # Notify admin_user_ids
        channel_mentions = "\n".join(f"  • <#{r['channel_id']}>（群組 **{r['group_name']}**）" for r in rows)
        message = (
            f"🤖 機器人已離開伺服器\n\n"
            f"**伺服器：** {guild.name}\n"
            f"**伺服器 ID：** {guild.id}\n\n"
            f"該伺服器包含以下中繼頻道，已自動從同步清單中移除：\n"
            f"{channel_mentions}"
        )
        await notify_admins(self.bot, "機器人離開伺服器", message)

        # Remove relay channels for this guild from DB
        channel_ids = [r["channel_id"] for r in rows]
        placeholders = ",".join("?" for _ in channel_ids)
        db.execute(f"DELETE FROM linked_channels WHERE channel_id IN ({placeholders})", channel_ids)

        log.info("GUILD", f"Removed {len(channel_ids)} relay channel(s) from guild {guild.id}")


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildTracker(bot))
