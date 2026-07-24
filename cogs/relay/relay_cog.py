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
from discord import Message, Embed, TextChannel
from discord.ext import commands

from app.config import RELAY_QUEUE_DELAY_MS
from database import DatabaseManager
from utils.log_manager import LogManager
from utils.time_utils import snowflake_before
from utils.admin_notifier import notify_admins
from app.config_sync import sync_configured_relays, load_config
from .queue import relay_queue
from .webhook import WebhookManager
from .routing import (
    linked_channel_id_for_message,
    configured_channel_id_for_stored_channel,
    webhook_thread_for_stored_channel,
    prepare_thread_route,
)

log = LogManager

_MAX_USERNAME_LENGTH = 80
_DISCORD_MSG_LIMIT = 2000
_MAX_EMBEDS = 10
_NO_MENTIONS = {"parse": []}

# Regex to detect Klipy GIF URLs that Discord didn't auto-embed
_KLiPY_RE = re.compile(r'https?://(?:www\\.)?klipy\\.com/gifs/\\S+', re.IGNORECASE)

# Regex to match custom emoji from other servers (Nitro)
_CUSTOM_EMOJI_RE = re.compile(r'<(a?):(\\w+):(\\d+)>')

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
        self.webhook_manager = WebhookManager()
        self._recently_deleted: set[str] = set()

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
            await self._sync_reverse_delete(str(message.id))
            return

        await self._sync_forward_delete(
            str(message.id),
            linked_channel_id_for_message(message),
        )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if not payload.guild_id:
            return
        message_id = str(payload.message_id)
        channel_id = str(payload.channel_id)
        if await self._sync_reverse_delete(message_id):
            return
        await self._sync_forward_delete(message_id, channel_id)

    async def _sync_reverse_delete(self, relayed_message_id: str) -> bool:
        db = DatabaseManager()
        link = db.fetchone(
            """SELECT original_message_id, original_channel_id
               FROM relayed_messages WHERE relayed_message_id = ?""",
            (relayed_message_id,),
        )
        if not link:
            return False
        orig_cfg = configured_channel_id_for_stored_channel(db, link["original_channel_id"])
        src = db.fetchone(
            "SELECT allow_reverse_delete FROM linked_channels WHERE channel_id = ?",
            (orig_cfg,),
        )
        if not src or not src["allow_reverse_delete"]:
            return True
        try:
            ch = await self.bot.fetch_channel(int(link["original_channel_id"]))
            orig = await ch.fetch_message(int(link["original_message_id"]))
            await orig.delete()
        except Exception:
            pass
        return True

    async def _sync_forward_delete(self, original_message_id: str, channel_id: str) -> bool:
        # Dedup: skip if we already processed this deletion
        if original_message_id in self._recently_deleted:
            return True
        self._recently_deleted.add(original_message_id)
        asyncio.get_running_loop().call_later(5, self._recently_deleted.discard, original_message_id)

        # Cancel any queued-but-not-yet-sent webhook payloads for this message
        relay_queue.cancel(original_message_id)

        db = DatabaseManager()
        src = db.fetchone(
            "SELECT allow_forward_delete FROM linked_channels WHERE channel_id = ?",
            (configured_channel_id_for_stored_channel(db, channel_id),),
        )
        if not src or not src["allow_forward_delete"]:
            return False

        relayed = db.fetchall(
            "SELECT relayed_message_id, relayed_channel_id FROM relayed_messages WHERE original_message_id = ?",
            (original_message_id,),
        )
        if not relayed:
            return False

        deleted = 0
        failed = 0
        for row in relayed:
            relayed_message_id = str(row["relayed_message_id"])
            try:
                cfg_id = configured_channel_id_for_stored_channel(db, row["relayed_channel_id"])
                link = db.fetchone(
                    "SELECT webhook_url FROM linked_channels WHERE channel_id = ?", (cfg_id,)
                )
                if not link or not link["webhook_url"]:
                    failed += 1
                    log.warn("DEL-FWD", f"Missing webhook for relayed channel {row['relayed_channel_id']} (cfg {cfg_id})")
                    continue
                wh = discord.Webhook.from_url(
                    link["webhook_url"],
                    session=self.bot.http._HTTPClient__session,
                )
                thread = webhook_thread_for_stored_channel(db, row["relayed_channel_id"])
                if thread:
                    await wh.delete_message(int(relayed_message_id), thread=thread)
                else:
                    await wh.delete_message(int(relayed_message_id))
                deleted += 1
                self._delete_relay_record(db, original_message_id, relayed_message_id)
            except discord.NotFound:
                deleted += 1
                self._delete_relay_record(db, original_message_id, relayed_message_id)
            except Exception as exc:
                failed += 1
                log.warn("DEL-FWD", f"Delete failed {relayed_message_id} in {row['relayed_channel_id']}: {exc}")

        log.info("DEL-FWD", f"Deleted {deleted}/{len(relayed)} relayed copies for {original_message_id}; failed={failed}")
        return True

    def _delete_relay_record(self, db: DatabaseManager, original_message_id: str, relayed_message_id: str) -> None:
        db.execute(
            """DELETE FROM relayed_messages
               WHERE original_message_id = ? AND relayed_message_id = ?""",
            (original_message_id, relayed_message_id),
        )
        db.commit()

    # ------------------------------------------------------------------
    # on_message_edit — edit sync
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_edit(self, before: Message, after: Message):
        message = after
        if not message.guild:
            return
        if message.type not in _RELAY_MESSAGE_TYPES:
            return
        if message.author.id == self.bot.user.id:
            return
        if message.webhook_id and message.application_id == self.bot.user.id:
            return

        db = DatabaseManager()
        link = db.fetchone(
            "SELECT 1 FROM relayed_messages WHERE original_message_id = ? LIMIT 1",
            (str(message.id),),
        )
        if not link:
            return

        source = db.fetchone(
            "SELECT * FROM linked_channels WHERE channel_id = ?",
            (linked_channel_id_for_message(message),),
        )
        if not source:
            return
        if not source["process_bot_messages"] and (message.author.bot or message.webhook_id):
            return

        group = db.fetchone("SELECT * FROM relay_groups WHERE group_id = ?", (source["group_id"],))
        is_owner = group and group["owner_user_id"] and str(message.author.id) == group["owner_user_id"]

        final_content = message.content or ""
        if final_content and not is_owner:
            filters = db.fetchall("SELECT phrase FROM group_filters WHERE group_id = ?", (source["group_id"],))
            for f in filters:
                final_content = re.sub(rf"\b{re.escape(f['phrase'])}\b", "***", final_content, flags=re.IGNORECASE)

        sender_name = message.author.display_name
        server_brand = source["brand_name"] or message.guild.name
        username = f"{sender_name} ({server_brand})"
        if len(username) > _MAX_USERNAME_LENGTH:
            username = username[:_MAX_USERNAME_LENGTH - 3] + "..."

        if len(final_content) > _DISCORD_MSG_LIMIT:
            final_content = final_content[:_DISCORD_MSG_LIMIT - 50] + "...(truncated)"

        payload_embeds = []
        for emb in message.embeds:
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
                for field in emb.fields:
                    clean.add_field(name=field.name, value=field.value, inline=field.inline)
            payload_embeds.append(clean)
        final_content = self._strip_embed_urls_from_content(final_content, message.embeds)
        final_content, payload_embeds = await self._resolve_klipy_urls(final_content, payload_embeds)
        final_content, payload_embeds = await self._resolve_custom_emojis(final_content, payload_embeds)
        final_content = self._append_attachment_previews(final_content, payload_embeds, message.attachments)

        relayed = db.fetchall(
            "SELECT relayed_message_id, relayed_channel_id FROM relayed_messages WHERE original_message_id = ?",
            (str(message.id),),
        )
        for row in relayed:
            try:
                cfg_id = configured_channel_id_for_stored_channel(db, row["relayed_channel_id"])
                link_info = db.fetchone(
                    "SELECT webhook_url, guild_id FROM linked_channels WHERE channel_id = ?", (cfg_id,)
                )
                if not link_info or not link_info["webhook_url"]:
                    continue
                wh = discord.Webhook.from_url(
                    link_info["webhook_url"],
                    session=self.bot.http._HTTPClient__session,
                )
                thread = webhook_thread_for_stored_channel(db, row["relayed_channel_id"])
                edit_kwargs = {
                    "content": final_content,
                    "embeds": payload_embeds,
                    "allowed_mentions": discord.AllowedMentions.none(),
                }
                if thread:
                    edit_kwargs["thread"] = thread
                await wh.edit_message(int(row["relayed_message_id"]), **edit_kwargs)
            except discord.NotFound:
                pass
            except Exception as exc:
                log.error("EDIT", f"Failed {row['relayed_message_id']}: {exc}")

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
        reply_embed = None
        final_content = filtered_content
        has_unmapped_roles = False

        # Reply reconstruction
        if original.reference and original.reference.message_id:
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

                reply_embed = Embed(color=0xB0B8C6, description=rc)
                reply_embed.set_author(
                    name=f"Replying to {ra}", url=link, icon_url=replied.author.display_avatar.url,
                )
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
        if original.message_snapshots:
            snap = original.message_snapshots[0]
            if snap.content:
                payload_content += f"\n> *Forwarded:*\n{snap.content}"
            if snap.embeds:
                payload_embeds.extend(snap.embeds)
            for att in snap.attachments:
                line = f"\n{att.url}"
                if len(payload_content) + len(line) <= _DISCORD_MSG_LIMIT - 50:
                    payload_content += line

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
        payload_content = self._strip_embed_urls_from_content(payload_content, original.embeds)
        payload_content, payload_embeds = await self._resolve_klipy_urls(payload_content, payload_embeds)
        payload_content, payload_embeds = await self._resolve_custom_emojis(payload_content, payload_embeds)
        payload_content = self._append_attachment_previews(payload_content, payload_embeds, original.attachments)
        if original.stickers:
            for s in original.stickers:
                if len(payload_embeds) >= _MAX_EMBEDS:
                    break
                embed = Embed(color=0x2B2D31)
                embed.set_image(url=s.url)
                payload_embeds.append(embed)
            if not payload_content.strip():
                payload_content = "\u200B"

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
            "replied_to_id": str(original.reference.message_id) if original.reference else None,
            "group_id": target["group_id"],
            **thread_route,
        }
        await relay_queue.add(target["webhook_url"], payload, meta)

    def _strip_embed_urls_from_content(self, content: str, embeds: list) -> str:
        """Remove bare URLs from content that are already represented as rich embeds."""
        embed_urls: set[str] = set()
        for emb in embeds:
            if emb.url:
                embed_urls.add(emb.url.rstrip("/"))
            if emb.image and emb.image.url:
                embed_urls.add(emb.image.url.rstrip("/"))
            if emb.thumbnail and emb.thumbnail.url:
                embed_urls.add(emb.thumbnail.url.rstrip("/"))
        if not embed_urls:
            return content
        for url in sorted(embed_urls, key=len, reverse=True):
            escaped = re.escape(url)
            content = re.sub(rf"\s*{escaped}\s*", " ", content).strip()
            content = re.sub(r"\s+", " ", content)
        return content

    def _append_attachment_previews(self, content: str, embeds: list, attachments) -> str:
        overflow: list[str] = []
        for att in sorted(attachments, key=lambda item: item.size):
            if self._is_image_attachment(att) and len(embeds) < _MAX_EMBEDS:
                embed = Embed(color=0x2B2D31)
                embed.set_image(url=att.url)
                embeds.append(embed)
                continue

            line = f"\n{att.url}"
            if len(content) + len(line) <= _DISCORD_MSG_LIMIT - 50:
                content += line
            else:
                overflow.append(att.filename)

        if overflow:
            content += f"\n*(Note: {len(overflow)} file(s) too large: {', '.join(overflow)})*"
        return content

    async def _resolve_klipy_urls(self, content: str, embeds: list) -> tuple[str, list]:
        """Find Klipy GIF URLs in content, fetch the actual GIF, add as embeds.

        Discord's GIF picker sometimes sends Klipy links without an embed.
        This fetches the og:image from the Klipy page so we can embed it.
        """
        urls = _KLiPY_RE.findall(content)
        if not urls:
            return content, embeds

        # Build set of already-embedded image URLs to avoid dupes
        existing: set[str] = set()
        for e in embeds:
            img = getattr(e, "image", None)
            if img and img.url:
                existing.add(img.url.rstrip("/"))

        new_embeds = list(embeds)
        async with aiohttp.ClientSession() as session:
            for url in urls:
                clean_url = url.rstrip("/")
                if clean_url in existing:
                    continue
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text()
                        # Try standard og:image
                        gif_url = None
                        m = re.search(
                            r'<meta\\s+property="og:image"\\s+content="([^"]+)"',
                            html, re.IGNORECASE,
                        )
                        if m:
                            gif_url = m.group(1)
                        else:
                            # Try reversed attribute order
                            m = re.search(
                                r'<meta\\s+content="([^"]+)"\\s+property="og:image"',
                                html, re.IGNORECASE,
                            )
                            if m:
                                gif_url = m.group(1)
                        if gif_url and len(new_embeds) < _MAX_EMBEDS:
                            # Klipy sometimes serves og:image as .mp4 — Discord
                            # can't auto-play MP4 in an embed image field.
                            # Try to find a static image version instead.
                            if gif_url.lower().endswith('.mp4'):
                                found = False
                                for ext in ('.gif', '.png', '.webp'):
                                    test_url = re.sub(r'\.mp4$', ext, gif_url, flags=re.IGNORECASE)
                                    try:
                                        async with session.head(
                                            test_url,
                                            timeout=aiohttp.ClientTimeout(total=3),
                                        ) as tresp:
                                            if tresp.status == 200:
                                                gif_url = test_url
                                                found = True
                                                break
                                    except Exception:
                                        continue
                                if not found:
                                    # No static version — put clickable link back in content
                                    content += f"\n{clean_url}"
                                    continue
                            embed = Embed(color=0x2B2D31)
                            embed.set_image(url=gif_url)
                            new_embeds.append(embed)
                            existing.add(gif_url.rstrip("/"))
                except Exception:
                    pass

        # Strip Klipy URLs from content
        for url in urls:
            content = content.replace(url, "").strip()
        content = re.sub(r"\\s+", " ", content).strip()

        return content, new_embeds

    async def _resolve_custom_emojis(self, content: str, embeds: list) -> tuple[str, list]:
        """Replace cross-server custom emoji (<:name:id> / <a:name:id>)
        with embed images sourced from Discord CDN."""
        matches = list(_CUSTOM_EMOJI_RE.finditer(content))
        if not matches:
            return content, embeds

        existing: set[str] = set()
        for e in embeds:
            img = getattr(e, "image", None)
            if img and img.url:
                existing.add(img.url.rstrip("/"))

        new_embeds = list(embeds)
        for m in matches:
            animated = m.group(1) == "a"
            emoji_id = m.group(3)
            ext = "gif" if animated else "png"
            cdn_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
            if cdn_url.rstrip("/") in existing:
                continue
            if len(new_embeds) >= _MAX_EMBEDS:
                break
            embed = Embed(color=0x2B2D31)
            embed.set_image(url=cdn_url)
            new_embeds.append(embed)
            existing.add(cdn_url.rstrip("/"))

        # Strip all emoji codes from content
        content = _CUSTOM_EMOJI_RE.sub("", content).strip()
        content = re.sub(r"\\s+", " ", content).strip()

        return content, new_embeds

    def _is_image_attachment(self, attachment) -> bool:
        content_type = getattr(attachment, "content_type", None) or ""
        if content_type.startswith("image/"):
            return True
        filename = getattr(attachment, "filename", "").lower()
        return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))

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


async def setup(bot: commands.Bot):
    await bot.add_cog(RelayCog(bot))
