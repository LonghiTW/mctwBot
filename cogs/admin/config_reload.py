"""Admin command to reload relay configuration from config.json without restart."""
import discord
from discord.ext import commands

from database import DatabaseManager
from utils.log_manager import LogManager
from utils.time_utils import snowflake_before
from app.config_sync import sync_configured_relays, load_config

log = LogManager


class ConfigReload(commands.Cog):
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
    # !reload
    # ------------------------------------------------------------------
    @commands.command(name="reload")
    async def reload_config(self, ctx: commands.Context):
        """重新載入 config.json 設定，不須重啟機器人。"""
        if not self._is_admin(ctx.author):
            await ctx.send("❌ 只有 admin.user_ids 中的管理員才能使用此指令。")
            return
        try:
            db = DatabaseManager()

            # -------- 1. Snapshot old channels --------
            old_rows = db.fetchall(
                """SELECT lc.channel_id, lc.guild_id, lc.group_id, rg.group_name
                   FROM linked_channels lc
                   JOIN relay_groups rg ON rg.group_id = lc.group_id"""
            )
            old_by_group: dict[str, set[str]] = {}
            for r in old_rows:
                gname = r["group_name"]
                old_by_group.setdefault(gname, set()).add(r["channel_id"])

            # -------- 2. Sync --------
            await sync_configured_relays(self.bot)

            # -------- 3. Snapshot new channels --------
            new_rows = db.fetchall(
                """SELECT lc.channel_id, lc.guild_id, lc.group_id, rg.group_name
                   FROM linked_channels lc
                   JOIN relay_groups rg ON rg.group_id = lc.group_id"""
            )
            new_by_group: dict[str, set[str]] = {}
            for r in new_rows:
                gname = r["group_name"]
                new_by_group.setdefault(gname, set()).add(r["channel_id"])

            # Build guild lookup for channel links
            channel_guild: dict[str, str] = {}
            for r in old_rows + new_rows:
                channel_guild[r["channel_id"]] = r["guild_id"]

            # Re-apply prune_days
            self._prune_old_messages()

            # -------- 5. Compute diffs per group --------
            all_group_names = set(old_by_group) | set(new_by_group)
            has_any_change = False

            for gname in sorted(all_group_names):
                old_set = old_by_group.get(gname, set())
                new_set = new_by_group.get(gname, set())

                added = new_set - old_set
                removed = old_set - new_set

                if not added and not removed:
                    continue
                has_any_change = True

                # Kept channels (remain in both old and new)
                kept = old_set & new_set

                # --- Notify kept channels about additions & removals ---
                msg_parts = [f"**{gname} 頻道更新**"]
                for cid in sorted(added):
                    gid = channel_guild.get(cid, "?")
                    msg_parts.append(f"  ➕ 新增 https://discord.com/channels/{gid}/{cid}")
                for cid in sorted(removed):
                    gid = channel_guild.get(cid, "?")
                    msg_parts.append(f"  ➖ 移除 https://discord.com/channels/{gid}/{cid}")
                notify_text = "\n".join(msg_parts)

                for cid in kept:
                    ch = self.bot.get_channel(int(cid))
                    if ch is None:
                        try:
                            ch = await self.bot.fetch_channel(int(cid))
                        except Exception:
                            continue
                    if hasattr(ch, "send"):
                        try:
                            await ch.send(notify_text)
                        except Exception:
                            pass

                # --- Welcome new channels ---
                for cid in sorted(added):
                    other_in_group = [
                        f"https://discord.com/channels/{channel_guild.get(oc, '?')}/{oc}"
                        for oc in sorted(new_set) if oc != cid
                    ]
                    other_text = "、".join(other_in_group) if other_in_group else "無"
                    welcome = (
                        f"👋 此頻道已加入麥塊聯盟的群組 **{gname}**。\n"
                        f"群組內其他頻道：{other_text}"
                    )
                    ch = self.bot.get_channel(int(cid))
                    if ch is None:
                        try:
                            ch = await self.bot.fetch_channel(int(cid))
                        except Exception:
                            continue
                    if hasattr(ch, "send"):
                        try:
                            await ch.send(welcome)
                        except Exception:
                            pass

            # -------- 6. Respond to command --------
            if not has_any_change:
                await ctx.send("✅ 設定已重新載入，無頻道變更。")
                return

            summary = "✅ 設定已重新載入。\n\n**頻道變更：**\n"
            for gname in sorted(all_group_names):
                old_set = old_by_group.get(gname, set())
                new_set = new_by_group.get(gname, set())
                added = new_set - old_set
                removed = old_set - new_set
                if not added and not removed:
                    continue
                summary += f"**{gname}**\n"
                for cid in sorted(added):
                    gid = channel_guild.get(cid, "?")
                    summary += f"  ➕ https://discord.com/channels/{gid}/{cid}\n"
                for cid in sorted(removed):
                    gid = channel_guild.get(cid, "?")
                    summary += f"  ➖ https://discord.com/channels/{gid}/{cid}\n"
                summary += "\n"

            await ctx.send(summary[:1900])
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
    await bot.add_cog(ConfigReload(bot))
