"""
RelayCog — cross-server message relay, edit/delete sync, thread/forum sync.

Consolidates all relay event handlers into a single Cog.
"""
import asyncio
import re
import secrets
from datetime import datetime

import aiohttp
import discord
from discord import Message, Embed
from discord.ext import commands

from database import DatabaseManager
from utils.log_manager import LogManager
from utils.time_utils import snowflake_before
from utils.admin_notifier import notify_admins
from app.config_sync import sync_configured_relays, load_config
from .queue import relay_queue
from .routing import (
    linked_channel_id_for_message,
    configured_channel_id_for_stored_channel,
    prepare_thread_route,
)
from .rendering import (
    append_attachment_previews,
    build_reply_embed,
    format_referenced_message_text,
    resolve_klipy_urls,
    strip_embed_urls_from_content,
)
from .reactions import ReactionSync
from .emoji_resolver import EmojiResolver
from .message_sync import MessageSync
from .edit_sync import EditSync

log = LogManager

_MAX_USERNAME_LENGTH = 80
_DISCORD_MSG_LIMIT = 2000
_NO_MENTIONS = {"parse": []}

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
            await self._mirror_thread_from_relayed_message(message.channel)

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
                        self._track_filter_violation(db, message, source, group, f, exec_id)

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
        try:
            if thread.me is None:
                await thread.join()
                log.info("THREAD", f"Joined new thread {thread.id} ({thread.name})")
        except Exception as exc:
            log.warn("THREAD", f"Failed to join thread {thread.id}: {exc}")

        if await self._mirror_thread_from_relayed_message(thread):
            return

    # ------------------------------------------------------------------
    # on_thread_update — lock / archive / name sync
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if (before.locked == after.locked
                and before.archived == after.archived
                and before.name == after.name):
            return

        db = DatabaseManager()

        # Skip if this is a target thread (prevent echo)
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

        for m in mappings:
            try:
                target = self.bot.get_channel(int(m["target_thread_id"]))
                if target is None:
                    target = await self.bot.fetch_channel(int(m["target_thread_id"]))
                await target.edit(**kwargs)
            except discord.NotFound:
                db.execute("DELETE FROM relay_threads WHERE target_thread_id = ?", (m["target_thread_id"],))
                db.commit()
            except Exception as exc:
                log.error("THR-UPD", f"Failed {m['target_thread_id']}: {exc}")

    # ------------------------------------------------------------------
    # on_thread_delete — delete mirrored threads
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        db = DatabaseManager()
        mappings = db.fetchall(
            "SELECT * FROM relay_threads WHERE source_thread_id = ?", (str(thread.id),)
        )
        if not mappings:
            return

        for m in mappings:
            try:
                target = self.bot.get_channel(int(m["target_thread_id"]))
                if target is None:
                    target = await self.bot.fetch_channel(int(m["target_thread_id"]))
                if target:
                    await target.delete()
            except discord.NotFound:
                pass
            except Exception as exc:
                log.error("THR-DEL", f"Failed {m['target_thread_id']}: {exc}")

        db.execute(
            "DELETE FROM relay_threads WHERE source_thread_id = ?", (str(thread.id),)
        )
        db.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _mirror_thread_from_relayed_message(self, thread: discord.Thread) -> bool:
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

        payload_content = final_content

        payload_embeds = []
        snapshot_attachments = []
        if original.message_snapshots:
            snap = original.message_snapshots[0]
            forward_text = f"↱ {format_referenced_message_text(snap.content, snap.attachments)}"
            ref = original.reference
            ref_guild_id = getattr(ref, "guild_id", None) or original.guild.id
            ref_channel_id = getattr(ref, "channel_id", None)
            ref_message_id = getattr(ref, "message_id", None)
            if ref_channel_id and ref_message_id:
                forward_url = f"https://discord.com/channels/{ref_guild_id}/{ref_channel_id}/{ref_message_id}"
                payload_content += f"\n> Forwarded from {forward_url}\n{forward_text}"
            else:
                payload_content += f"\n> Forwarded\n{forward_text}"

            if snap.embeds:
                payload_embeds.extend(snap.embeds)
            snapshot_attachments = list(snap.attachments)

        if original.poll:
            poll_embed = Embed(color=0x5865F2)
            poll_embed.set_author(name="📊 Poll")
            poll_embed.title = original.poll.question[:256]
            desc = []
            for i, ans in enumerate(original.poll.answers):
                emoji = ans.emoji or f"{i+1}."
                desc.append(f"{emoji} **{ans.text}**")
            poll_embed.description = "\n".join(desc)[:4096]
            payload_embeds.append(poll_embed)

        if len(payload_content) > _DISCORD_MSG_LIMIT:
            payload_content = payload_content[:_DISCORD_MSG_LIMIT - 50] + "...(truncated)"

        if reply_embed:
            payload_embeds.append(reply_embed)

        for emb in original.embeds:
            clean = Embed(
                title=emb.title,
                description=emb.description[:4096] if emb.description else None,
                color=emb.color, url=emb.url, timestamp=emb.timestamp,
            )
            if emb.author:
                clean.set_author(name=emb.author.name, url=emb.author.url, icon_url=emb.author.icon_url)
            if emb.footer:
                clean.set_footer(text=emb.footer.text, icon_url=emb.footer.icon_url)
            if emb.image:
                clean.set_image(url=emb.image.url)
            if emb.thumbnail:
                clean.set_thumbnail(url=emb.thumbnail.url)
            if emb.fields:
                for f in emb.fields:
                    clean.add_field(name=f.name, value=f.value, inline=f.inline)
            payload_embeds.append(clean)
        payload_content, payload_embeds = await resolve_klipy_urls(payload_content, payload_embeds)
        payload_content = strip_embed_urls_from_content(payload_content, original.embeds)
        target_guild = self.bot.get_guild(int(target["guild_id"]))
        payload_content, payload_embeds = await self.emoji_resolver.resolve_content(payload_content, payload_embeds, target_guild)
        all_attachments = list(original.attachments) + snapshot_attachments
        payload_content, relay_files = append_attachment_previews(payload_content, payload_embeds, all_attachments)

        # Download images for multipart upload (grid layout), fallback to URL on failure
        files_for_upload: list[dict] = []
        if relay_files:
            async with aiohttp.ClientSession() as dl_session:
                for rf in relay_files:
                    try:
                        async with dl_session.get(
                            rf["url"], timeout=aiohttp.ClientTimeout(total=10)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                if len(data) < 8_000_000:  # Discord 8 MB limit
                                    files_for_upload.append({
                                        "filename": rf["filename"],
                                        "data": data,
                                        "content_type": rf["content_type"],
                                    })
                                    continue
                    except Exception:
                        pass
                    # Fallback: put clean URL (no signature params) in content
                    clean_url = rf["url"].split("?")[0]
                    line = f"\n{clean_url}"
                    if len(payload_content) + len(line) <= _DISCORD_MSG_LIMIT - 50:
                        payload_content += line
        if original.stickers:
            attachment_urls = {att.url.rstrip("/") for att in original.attachments}
            for s in original.stickers:
                if s.url.rstrip("/") in attachment_urls:
                    continue
                line = f"\n{s.url}"
                if len(payload_content) + len(line) <= _DISCORD_MSG_LIMIT - 50:
                    payload_content += line

        payload = {
            "content": payload_content,
            "username": username,
            "avatar_url": avatar_url,
            "embeds": [e.to_dict() if hasattr(e, "to_dict") else e for e in payload_embeds],
            "allowed_mentions": _NO_MENTIONS,
        }

        meta = {
            "original_msg_id": str(original.id),
            "original_channel_id": str(original.channel.id),
            "target_channel_id": target["channel_id"],
            "execution_id": exec_id,
            "replied_to_id": str(original.reference.message_id) if original.reference and not is_forward else None,
            "group_id": target["group_id"],
            "group_name": group["group_name"],
            **thread_route,
        }
        await relay_queue.add(target["webhook_url"], payload, meta, files=files_for_upload)

    def _track_filter_violation(
        self, db: DatabaseManager, message: Message,
        source: dict, group: dict, f: dict, exec_id: str,
    ):
        db.execute(
            """INSERT INTO user_warnings (group_id, user_id, filter_id, warning_count, last_violation_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(group_id, user_id, filter_id) DO UPDATE SET
                   warning_count = warning_count + 1,
                   last_violation_at = excluded.last_violation_at""",
            (source["group_id"], str(message.author.id), f["filter_id"], int(datetime.now().timestamp())),
        )
        db.commit()

        stats = db.fetchone(
            "SELECT warning_count FROM user_warnings WHERE group_id = ? AND user_id = ? AND filter_id = ?",
            (source["group_id"], str(message.author.id), f["filter_id"]),
        )
        wc = stats["warning_count"] if stats else 0
        threshold = f["threshold"]

        if threshold == 0:
            return
        elif threshold == 1:
            db.execute(
                """INSERT OR IGNORE INTO group_blacklist (group_id, blocked_id, type) VALUES (?, ?, 'USER')""",
                (source["group_id"], str(message.author.id)),
            )
            db.commit()
            return
        elif wc >= threshold:
            db.execute(
                """INSERT OR IGNORE INTO group_blacklist (group_id, blocked_id, type) VALUES (?, ?, 'USER')""",
                (source["group_id"], str(message.author.id)),
            )
            db.commit()
            asyncio.create_task(self._notify_ban(message, group, f, threshold))
            return
        else:
            remaining = threshold - wc
            asyncio.create_task(self._send_warning(
                message, message.author.id,
                f"⚠️ **Warning:** {f['warning_msg'] or 'Inappropriate language'}\n"
                f"Phrase: ||{f['phrase']}||\nStrikes: {wc}/{threshold} ({remaining} left).",
            ))

    async def _send_warning(self, destination, user_id: int, text: str):
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(f"⚠️ **Relay Warning**\nServer: {destination.guild.name}\n{text}")
        except Exception:
            try:
                msg = await destination.channel.send(f"<@{user_id}> {text}")
                await asyncio.sleep(15)
                await msg.delete()
            except Exception:
                pass

    async def _notify_ban(self, message: Message, group: dict, f: dict, threshold: int):
        await self._send_warning(
            message, message.author.id,
            f"🚫 **You have been blocked from the relay group.**\n"
            f"Reason: Repeated use of prohibited phrase: ||{f['phrase']}||",
        )
        await notify_admins(
            self.bot, "🚫 成員被自動封鎖",
            f"**使用者：** {message.author}（{message.author.id}）\n"
            f"**伺服器：** {message.guild.name}\n"
            f"**群組：** {group['group_name']}\n"
            f"**原因：** 觸發過濾器「{f['phrase']}」達上限（{threshold} 次）",
        )

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------
    def _is_config_admin(self, member: discord.User | discord.Member | None) -> bool:
        if member is None:
            return False
        admin_ids = {int(uid) for uid in load_config().get("admin", {}).get("user_ids", [])}
        return member.id in admin_ids

    def _can_view_relaylist(self, author: discord.User | discord.Member | None) -> bool:
        """relaylist 權限：config admin 或 guild administrator/manage_guild。"""
        if author is None:
            return False
        if self._is_config_admin(author):
            return True
        if isinstance(author, discord.Member) and author.guild:
            return author.guild_permissions.manage_guild or author.guild_permissions.administrator
        return False

    def _format_channel_link(self, guild_id: str, channel_id: str) -> str:
        guild_name = str(guild_id)
        channel_name = str(channel_id)

        try:
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                guild_name = guild.name
                channel = guild.get_channel(int(channel_id))
                if channel:
                    channel_name = channel.name
            else:
                channel = self.bot.get_channel(int(channel_id))
                if channel:
                    channel_name = channel.name
                    if getattr(channel, "guild", None):
                        guild_name = channel.guild.name
        except Exception:
            pass

        return f"[{guild_name}](https://discord.com/channels/{guild_id}/{channel_id})"

    # ------------------------------------------------------------------
    # on_bot_reload — react to config reload (diff + notifications)
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_bot_reload(
        self,
        old_rows: list[dict],
        new_rows: list[dict],
    ):
        """Called after !reload syncs config. Computes diff and notifies channels."""
        old_by_group: dict[str, set[str]] = {}
        for r in old_rows:
            old_by_group.setdefault(r["group_name"], set()).add(r["channel_id"])

        new_by_group: dict[str, set[str]] = {}
        for r in new_rows:
            new_by_group.setdefault(r["group_name"], set()).add(r["channel_id"])

        channel_guild: dict[str, str] = {}
        for r in old_rows + new_rows:
            channel_guild[r["channel_id"]] = r["guild_id"]

        all_group_names = set(old_by_group) | set(new_by_group)

        for gname in sorted(all_group_names):
            old_set = old_by_group.get(gname, set())
            new_set = new_by_group.get(gname, set())

            added = new_set - old_set
            removed = old_set - new_set
            kept = old_set & new_set

            if not added and not removed:
                continue

            # Notify kept channels about additions & removals
            msg_parts = [f"**{gname} 頻道更新**"]
            for cid in sorted(added):
                gid = channel_guild.get(cid, "?")
                msg_parts.append(f"  ➕ 新增 {self._format_channel_link(gid, cid)}")
            for cid in sorted(removed):
                gid = channel_guild.get(cid, "?")
                msg_parts.append(f"  ➖ 移除 {self._format_channel_link(gid, cid)}")
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

            # Welcome new channels
            for cid in sorted(added):
                others = [
                    self._format_channel_link(channel_guild.get(oc, "?"), oc)
                    for oc in sorted(new_set) if oc != cid
                ]
                other_text = "、".join(others) if others else "無"
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

    @commands.command(name="relaylist")
    async def list_relays(self, ctx: commands.Context):
        """列出所有中繼群組與所屬頻道／伺服器。"""
        if not self._can_view_relaylist(ctx.author):
            await ctx.send("❌ 你沒有權限檢視中繼列表。僅限 admin.user_ids 或擁有「管理伺服器」權限者使用。")
            return

        # Read hidden flags from config.json
        config = load_config()
        relay_cfg = config.get("relay", {})
        hidden_groups: set[str] = set()
        for g_cfg in relay_cfg.get("groups", []):
            if g_cfg.get("hidden", False):
                hidden_groups.add(str(g_cfg.get("name", "")).strip())

        db = DatabaseManager()
        groups = db.fetchall("SELECT * FROM relay_groups ORDER BY group_name")
        if not groups:
            await ctx.send("目前沒有設定任何中繼群組。")
            return

        lines: list[str] = []
        for g in groups:
            if g["group_name"] in hidden_groups:
                continue

            channels = db.fetchall(
                "SELECT * FROM linked_channels WHERE group_id = ? ORDER BY guild_id, channel_id",
                (g["group_id"],),
            )
            lines.append(f"**{g['group_name']}**")

            if not channels:
                lines.append("  └ *無頻道*")
                continue

            for i, ch in enumerate(channels):
                prefix = "  └" if i == len(channels) - 1 else "  ├"
                d = "🔄" if ch["direction"] == "BOTH" else ("📤" if ch["direction"] == "SEND_ONLY" else "📥")
                lines.append(f"{prefix} {d} {self._format_channel_link(ch['guild_id'], ch['channel_id'])}")

            lines.append("")

        text = "\n".join(lines).strip()
        if not text:
            await ctx.send("目前沒有顯示任何中繼群組。")
            return

        if len(text) <= 1900:
            await ctx.send(text)
        else:
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
