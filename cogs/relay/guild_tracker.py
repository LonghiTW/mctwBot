"""Guild tracker — detects when the bot is removed/kicked from a guild.

Triggers an instant DM notification to bot admins with the ``notifications``
feature if the lost guild contained any relay-linked channels.
"""
import discord
from discord.ext import commands

from database import DatabaseManager
from app.config_sync import _purge_removed_channel_data
from app.relay_config_editor import (
    backup_config,
    channel_ids as configured_channel_ids,
    load_config_file,
    remove_channel,
    save_config_file,
)
from app.guild_config import guild_configs
from utils.log_manager import LogManager
from utils.admin_notifier import notify_admins

log = LogManager


def _remove_departed_guild_config(guild_id: int, channel_ids: set[str]) -> None:
    config = load_config_file()
    updated = config
    configured_ids = configured_channel_ids(config)
    removed = False
    for channel_id in channel_ids:
        if channel_id in configured_ids:
            updated, _, _ = remove_channel(updated, channel_id)
            removed = True

    relay_config = updated.get("relay", {})
    role_mappings = relay_config.get("role_mappings", []) if isinstance(relay_config, dict) else []
    retained_mappings = [
        mapping for mapping in role_mappings
        if str(mapping.get("guild_id", "")) != str(guild_id)
    ]
    if len(retained_mappings) != len(role_mappings):
        relay_config["role_mappings"] = retained_mappings
        removed = True

    if removed:
        backup_config()
        save_config_file(updated)


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
            try:
                _remove_departed_guild_config(guild.id, set())
            except Exception as exc:
                log.error("GUILD", f"Could not remove departed guild settings from config: {exc}")
            db.execute("DELETE FROM role_mappings WHERE guild_id = ?", (str(guild.id),))
            db.commit()
            guild_configs.remove_guild(guild.id)
            log.info("GUILD", f"No relay channels found in guild {guild.id}, skipping notification.")
            return

        # Notify bot admins with the notifications feature
        channel_mentions = "\n".join(f"  • <#{r['channel_id']}>（群組 **{r['group_name']}**）" for r in rows)
        message = (
            f"🤖 機器人已離開伺服器\n\n"
            f"**伺服器：** {guild.name}\n"
            f"**伺服器 ID：** {guild.id}\n\n"
            f"該伺服器包含以下中繼頻道，已自動從同步清單中移除：\n"
            f"{channel_mentions}"
        )
        await notify_admins(self.bot, "機器人離開伺服器", message)

        channel_ids = [str(r["channel_id"]) for r in rows]
        try:
            _remove_departed_guild_config(guild.id, set(channel_ids))
        except Exception as exc:
            log.error("GUILD", f"Could not remove departed guild channels from config: {exc}")

        _purge_removed_channel_data(db, set(channel_ids))
        placeholders = ",".join("?" for _ in channel_ids)
        db.execute(f"DELETE FROM linked_channels WHERE channel_id IN ({placeholders})", channel_ids)
        db.execute("DELETE FROM role_mappings WHERE guild_id = ?", (str(guild.id),))
        db.commit()
        guild_configs.remove_guild(guild.id)

        log.info("GUILD", f"Removed {len(channel_ids)} relay channel(s) from guild {guild.id}")


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildTracker(bot))
