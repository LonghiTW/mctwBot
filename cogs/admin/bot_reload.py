"""Admin command to reload bot configuration without restart.

Dispatches a custom ``on_bot_reload`` event so that other cogs
(e.g. relay) can react to configuration changes independently.
"""
import discord
from discord.ext import commands

from database import DatabaseManager
from utils.log_manager import LogManager
from utils.time_utils import snowflake_before
from app.config_sync import sync_configured_relays, load_config

log = LogManager


class BotReload(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Permission check
    # ------------------------------------------------------------------
    def _is_admin(self, member: discord.User | discord.Member | None) -> bool:
        if member is None:
            return False
        admin_ids = {int(uid) for uid in load_config().get("admin", {}).get("user_ids", [])}
        return member.id in admin_ids

    # ------------------------------------------------------------------
    # !reload — reload config and dispatch event
    # ------------------------------------------------------------------
    @commands.command(name="reload")
    async def reload_bot(self, ctx: commands.Context):
        """重新載入 config.json 設定，不須重啟機器人。"""
        if not self._is_admin(ctx.author):
            await ctx.send("❌ 只有 admin.user_ids 中的管理員才能使用此指令。")
            return
        try:
            db = DatabaseManager()

            # Snapshot DB state before sync
            old_rows = db.fetchall(
                """SELECT lc.channel_id, lc.guild_id, lc.group_id, rg.group_name
                   FROM linked_channels lc
                   JOIN relay_groups rg ON rg.group_id = lc.group_id"""
            )

            # Perform sync
            await sync_configured_relays(self.bot)

            # Snapshot DB state after sync
            new_rows = db.fetchall(
                """SELECT lc.channel_id, lc.guild_id, lc.group_id, rg.group_name
                   FROM linked_channels lc
                   JOIN relay_groups rg ON rg.group_id = lc.group_id"""
            )

            # Prune old records
            self._prune_old_messages()

            # Let other cogs react to the reload
            self.bot.dispatch("bot_reload", old_rows, new_rows)

            await ctx.send("✅ 設定已重新載入。")
        except Exception as exc:
            await ctx.send(f"❌ 載入失敗：{exc}")

    # ------------------------------------------------------------------
    # Prune old relay message records
    # ------------------------------------------------------------------
    def _prune_old_messages(self):
        config = load_config()
        days = config.get("relay", {}).get("prune_days", 7)
        if days <= 0:
            return
        db = DatabaseManager()
        cutoff = snowflake_before(days)
        result = db.execute(
            "DELETE FROM relayed_messages WHERE original_message_id < ?",
            (cutoff,),
        )
        db.commit()
        log.info("DB-PRUNE", f"Pruned {result.rowcount} old records.")


async def setup(bot: commands.Bot):
    await bot.add_cog(BotReload(bot))
