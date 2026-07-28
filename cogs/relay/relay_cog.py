"""
RelayCog — cross-server message relay, edit/delete sync, thread/forum sync.

Consolidates all relay event handlers into a single Cog.
"""
import re
import secrets

import discord
from discord import Message, Embed
from discord.ext import commands

from database import DatabaseManager
from utils.log_manager import LogManager
from utils.time_utils import snowflake_before
from app.config_sync import sync_configured_relays, load_config
from .queue import relay_queue
from .routing import (
    linked_channel_id_for_message,
    prepare_thread_route,
)
from .rendering import (
    build_reply_embed,
)
from .payload_builder import RelayPayloadBuilder
from .reactions import ReactionSync
from .emoji_resolver import EmojiResolver
from .message_sync import MessageSync
from .edit_sync import EditSync
from .thread_sync import ThreadSync
from .filters import RelayFilters
from .admin_views import RelayAdminViews

log = LogManager

_MAX_USERNAME_LENGTH = 80

# Only relay these message types — filter out system messages that cause echo loops
_RELAY_MESSAGE_TYPES = frozenset({
    discord.MessageType.default,
    discord.MessageType.reply,
})


class RelayCog(commands.Cog):
    """Handles all relay-related events: message relay, delete/edit sync,
    thread/forum lifecycle sync."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        relay_queue.set_client(bot)
        self.emoji_resolver = EmojiResolver(bot)
        self.reactions = ReactionSync(bot, self.emoji_resolver.resolve_reaction)
        self.message_sync = MessageSync(bot)
        self.edit_sync = EditSync(bot, self.emoji_resolver.resolve_content)
        self.thread_sync = ThreadSync(bot)
        self.filters = RelayFilters(bot)
        self.payload_builder = RelayPayloadBuilder(bot, self.emoji_resolver.resolve_content)
        self.admin_views = RelayAdminViews(bot)

    # ------------------------------------------------------------------
    # on_ready — sync config and prune DB
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        log.info("RELAY", f"RelayCog ready — {self.bot.user}")
        # Reload config from DB
        DatabaseManager()  # ensure migrations run
        await sync_configured_relays(self.bot)
        self._prune_old_messages()

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

    # ------------------------------------------------------------------
    # on_message — core relay logic
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if not message.guild:
            return
        if message.type not in _RELAY_MESSAGE_TYPES:
            return
        if message.author.id == self.bot.user.id:
            return
        if message.webhook_id and message.application_id == self.bot.user.id:
            return

        db = DatabaseManager()
        source_channel_id = linked_channel_id_for_message(message)
        source = db.fetchone(
            """SELECT * FROM linked_channels
               WHERE channel_id = ? AND direction IN ('BOTH', 'SEND_ONLY')""",
            (source_channel_id,),
        )
        if not source:
            return

        # Auto-join threads so future messages in it are received
        if isinstance(message.channel, discord.Thread):
            try:
                if message.channel.me is None:
                    await message.channel.join()
                    log.info("THREAD", f"Joined thread {message.channel.id} via on_message")
            except Exception:
                pass

        if not source["process_bot_messages"] and (message.author.bot or message.webhook_id):
            return

        if isinstance(message.channel, discord.Thread):
            await self.thread_sync.mirror_thread_from_relayed_message(message.channel)

        exec_id = secrets.token_hex(4)

        # Blacklist check
        blocked = db.fetchone(
            """SELECT 1 FROM group_blacklist
               WHERE group_id = ? AND (blocked_id = ? OR blocked_id = ?)""",
            (source["group_id"], str(message.author.id), str(message.guild.id)),
        )
        if blocked:
            return

        # Group info
        group = db.fetchone(
            "SELECT * FROM relay_groups WHERE group_id = ?",
            (source["group_id"],),
        )
        if not group:
            return

        # Filter system
        is_owner = group["owner_user_id"] and str(message.author.id) == group["owner_user_id"]
        final_content = message.content or ""
        if final_content:
            filters = db.fetchall(
                "SELECT * FROM group_filters WHERE group_id = ?",
                (source["group_id"],),
            )
            for f in filters:
                pattern = re.compile(rf"\b{re.escape(f['phrase'])}\b", re.IGNORECASE)
                if pattern.search(final_content):
                    final_content = pattern.sub("***", final_content)
                    if not is_owner:
                        self.filters.track_violation(db, message, source, group, f, exec_id)

        # Build sender identity
        sender_name = message.author.display_name
        server_brand = source["brand_name"] or message.guild.name
        username = f"{sender_name} ({server_brand})"
        if len(username) > _MAX_USERNAME_LENGTH:
            username = username[:_MAX_USERNAME_LENGTH - 3] + "..."
        avatar_url = message.author.display_avatar.url

        # Gather targets
        raw_targets = db.fetchall(
            """SELECT * FROM linked_channels
               WHERE group_id = ? AND channel_id != ?
               AND direction IN ('BOTH', 'RECEIVE_ONLY')""",
            (source["group_id"], source_channel_id),
        )
        target_map = {t["channel_id"]: t for t in raw_targets}
        targets = list(target_map.values())
        if not targets:
            return

        log.info("RELAY", f"Relaying {message.id} to {len(targets)} channel(s)", exec_id)

        for target in targets:
            try:
                thread_route = await prepare_thread_route(
                    self.bot, db, source["group_id"], message, target["channel_id"],
                )
                await self._relay_to_target(
                    message, source, target, group, username, avatar_url,
                    final_content, exec_id, thread_route,
                )
            except Exception as exc:
                log.error("RELAY", f"Failed to target {target['channel_id']}: {exc}", exec_id)

    # ------------------------------------------------------------------
    # on_message_delete — forward & reverse delete sync
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_delete(self, message: Message):
        if not message.guild:
            return
        if message.webhook_id:
            await self.message_sync.sync_reverse_delete(str(message.id))
            return

        await self.message_sync.sync_forward_delete(
            str(message.id),
            linked_channel_id_for_message(message),
        )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if not payload.guild_id:
            return
        message_id = str(payload.message_id)
        channel_id = str(payload.channel_id)
        if await self.message_sync.sync_reverse_delete(message_id):
            return
        await self.message_sync.sync_forward_delete(message_id, channel_id)

    # ------------------------------------------------------------------
    # on_message_edit — edit sync
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message):
        await self.edit_sync.sync_edit(after, _RELAY_MESSAGE_TYPES)

    # ------------------------------------------------------------------
    # on_thread_create — auto-join threads so relay can see their messages
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        await self.thread_sync.handle_thread_create(thread)

    # ------------------------------------------------------------------
    # on_thread_update — lock / archive / name sync
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        await self.thread_sync.handle_thread_update(before, after)

    # ------------------------------------------------------------------
    # on_thread_delete — delete mirrored threads
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        await self.thread_sync.handle_thread_delete(thread)

    async def _relay_to_target(
        self,
        original: Message, source: dict, target: dict, group: dict,
        username: str, avatar_url: str, filtered_content: str,
        exec_id: str, thread_route: dict,
    ):
        is_forward = bool(original.message_snapshots)
        reply_embed = None
        final_content = filtered_content
        has_unmapped_roles = False

        # Reply reconstruction
        if not is_forward and original.reference and original.reference.message_id:
            replied = None
            try:
                replied = await original.channel.fetch_message(original.reference.message_id)
            except Exception:
                pass

            # If fetch failed and channel is a thread, try parent channel
            if replied is None and isinstance(original.channel, discord.Thread) and original.channel.parent:
                try:
                    replied = await original.channel.parent.fetch_message(original.reference.message_id)
                except Exception:
                    pass

            if replied is None:
                reply_embed = Embed(color=0xB0B8C6, description="*Replying to a deleted message.*")

            if replied:
                ra = replied.author.display_name
                rc = (replied.content or "*(No text)*")[:1000]
                if replied.edited_at:
                    rc += " *(edited)*"

                db = DatabaseManager()
                parent_rec = db.fetchone(
                    "SELECT original_message_id FROM relayed_messages WHERE relayed_message_id = ?",
                    (str(replied.id),),
                )
                root_id = parent_rec["original_message_id"] if parent_rec else str(replied.id)
                copy = db.fetchone(
                    """SELECT relayed_message_id FROM relayed_messages
                       WHERE original_message_id = ? AND relayed_channel_id = ?""",
                    (root_id, target["channel_id"]),
                )
                link = f"https://discord.com/channels/{target['guild_id']}/{target['channel_id']}/{copy['relayed_message_id']}" if copy else str(replied.jump_url)

                reply_embed = build_reply_embed(replied, link, deleted=False)
        # Role mention mapping
        target_content = original.content or ""
        target_guild = self.bot.get_guild(int(target["guild_id"]))
        role_mentions = re.findall(r"<@&(\d+)>", target_content)
        if role_mentions:
            can_manage = target_guild and target_guild.me and target_guild.me.guild_permissions.manage_roles
            allow_auto = False
            if can_manage:
                ch = DatabaseManager().fetchone(
                    "SELECT allow_auto_role_creation FROM linked_channels WHERE channel_id = ?",
                    (target["channel_id"],),
                )
                allow_auto = ch and ch["allow_auto_role_creation"]

            db = DatabaseManager()
            for mention in role_mentions:
                role_map = db.fetchone(
                    """SELECT role_name FROM role_mappings
                       WHERE group_id = ? AND guild_id = ? AND role_id = ?""",
                    (source["group_id"], str(original.guild.id), mention),
                )
                if not role_map:
                    continue
                target_role = db.fetchone(
                    """SELECT role_id FROM role_mappings
                       WHERE group_id = ? AND guild_id = ? AND role_name = ?""",
                    (target["group_id"], target["guild_id"], role_map["role_name"]),
                )
                if target_role:
                    target_content = target_content.replace(f"<@&{mention}>", f"<@&{target_role['role_id']}>")
                elif allow_auto and target_guild:
                    try:
                        nr = await target_guild.create_role(
                            name=role_map["role_name"], mentionable=False,
                            reason="Relay auto-create",
                        )
                        db.execute(
                            """INSERT INTO role_mappings (group_id, guild_id, role_name, role_id)
                               VALUES (?, ?, ?, ?)""",
                            (target["group_id"], target["guild_id"], role_map["role_name"], str(nr.id)),
                        )
                        db.commit()
                        target_content = target_content.replace(f"<@&{mention}>", f"<@&{nr.id}>")
                    except Exception:
                        has_unmapped_roles = True
                else:
                    has_unmapped_roles = True

        final_content = target_content
        content_no_mentions = re.sub(r"<@!?&?#?(\d+)>", "", final_content).strip()
        if not content_no_mentions and has_unmapped_roles:
            final_content = "*(Unmapped role in original. Admin can map it or enable auto-sync.)*"

        payload, meta, files_for_upload = await self.payload_builder.build(
            original, target, group, username, avatar_url, final_content,
            reply_embed, is_forward, exec_id, thread_route,
        )
        await relay_queue.add(target["webhook_url"], payload, meta, files=files_for_upload)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_bot_reload(
        self,
        old_rows: list[dict],
        new_rows: list[dict],
    ):
        """Called after !reload syncs config. Computes diff and notifies channels."""
        update_messages, welcome_messages = self.admin_views.build_reload_notifications(old_rows, new_rows)
        for channel_id, text in update_messages + welcome_messages:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(int(channel_id))
                except Exception:
                    continue
            if hasattr(channel, "send"):
                try:
                    await channel.send(text)
                except Exception:
                    pass

    @commands.command(name="relaylist")
    async def list_relays(self, ctx: commands.Context):
        """列出所有中繼群組與所屬頻道／伺服器。"""
        if not self.admin_views.can_view_relaylist(ctx.author):
            await ctx.send("❌ 你沒有權限檢視中繼列表。僅限 admin.user_ids 或擁有「管理伺服器」權限者使用。")
            return

        chunks = self.admin_views.relaylist_chunks()
        if not chunks:
            await ctx.send("目前沒有設定任何中繼群組。")
            return

        for chunk in chunks:
            await ctx.send(chunk)

    # ------------------------------------------------------------------
    # on_raw_reaction_add / remove — sync reactions across relay channels
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Sync a reaction added to a relayed message across all copies."""
        if payload.user_id == self.bot.user.id:
            return
        await self.reactions.sync(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Sync a reaction removed from a relayed message across all copies."""
        if payload.user_id == self.bot.user.id:
            return
        await self.reactions.sync(payload, add=False)

async def setup(bot: commands.Bot):
    await bot.add_cog(RelayCog(bot))
