"""
RelayCog — cross-server message relay, edit/delete sync, thread/forum sync.

Consolidates all relay event handlers into a single Cog.
"""
import asyncio
import re
import secrets
from datetime import datetime

import discord
from discord import Message, Embed, TextChannel
from discord.ext import commands

from config import RELAY_QUEUE_DELAY_MS
from database import DatabaseManager
from utils.log_manager import LogManager
from utils.time_utils import snowflake_before
from utils.admin_notifier import notify_admins
from config_sync import sync_configured_relays, load_config
from .queue import relay_queue
from .webhook import WebhookManager
from .routing import (
    linked_channel_id_for_message,
    configured_channel_id_for_stored_channel,
    prepare_thread_route,
)

log = LogManager

_MAX_USERNAME_LENGTH = 80
_DISCORD_MSG_LIMIT = 2000

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

        # Auto-join threads so future messages in it are received
        if isinstance(message.channel, discord.Thread):
            try:
                if message.channel.me is None:
                    await message.channel.join()
                    log.info("THREAD", f"Joined thread {message.channel.id} via on_message")
            except Exception:
                pass

        db = DatabaseManager()
        source_channel_id = linked_channel_id_for_message(message)
        source = db.fetchone(
            """SELECT * FROM linked_channels
               WHERE channel_id = ? AND direction IN ('BOTH', 'SEND_ONLY')""",
            (source_channel_id,),
        )
        if not source:
            return
        if not source["process_bot_messages"] and (message.author.bot or message.webhook_id):
            return

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
        db = DatabaseManager()

        # Case 1: deleted message is a relayed webhook → reverse delete
        if message.webhook_id:
            link = db.fetchone(
                """SELECT original_message_id, original_channel_id
                   FROM relayed_messages WHERE relayed_message_id = ?""",
                (str(message.id),),
            )
            if not link:
                return
            orig_cfg = configured_channel_id_for_stored_channel(db, link["original_channel_id"])
            src = db.fetchone(
                "SELECT allow_reverse_delete FROM linked_channels WHERE channel_id = ?",
                (orig_cfg,),
            )
            if not src or not src["allow_reverse_delete"]:
                return
            try:
                ch = await self.bot.fetch_channel(int(link["original_channel_id"]))
                orig = await ch.fetch_message(int(link["original_message_id"]))
                await orig.delete()
            except Exception:
                pass
            return

        # Case 2: deleted message is an original → forward delete
        src = db.fetchone(
            "SELECT allow_forward_delete FROM linked_channels WHERE channel_id = ?",
            (linked_channel_id_for_message(message),),
        )
        if not src or not src["allow_forward_delete"]:
            return

        relayed = db.fetchall(
            "SELECT relayed_message_id, relayed_channel_id FROM relayed_messages WHERE original_message_id = ?",
            (str(message.id),),
        )
        if not relayed:
            return

        for row in relayed:
            try:
                cfg_id = configured_channel_id_for_stored_channel(db, row["relayed_channel_id"])
                link = db.fetchone(
                    "SELECT webhook_url FROM linked_channels WHERE channel_id = ?", (cfg_id,)
                )
                if not link or not link["webhook_url"]:
                    continue
                wh = discord.Webhook.from_url(
                    link["webhook_url"],
                    session=self.bot.http._HTTPClient__session,
                )
                await wh.delete_message(int(row["relayed_message_id"]))
            except discord.NotFound:
                pass
            except Exception as exc:
                log.warn("DEL-FWD", f"Delete failed {row['relayed_message_id']}: {exc}")

        db.execute(
            "DELETE FROM relayed_messages WHERE original_message_id = ?",
            (str(message.id),),
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

        if message.attachments:
            links = "\n".join(a.url for a in message.attachments)
            if links not in final_content:
                final_content += f"\n{links}"
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

        relayed = db.fetchall(
            "SELECT relayed_message_id, relayed_channel_id FROM relayed_messages WHERE original_message_id = ?",
            (str(message.id),),
        )
        for row in relayed:
            try:
                cfg_id = configured_channel_id_for_stored_channel(db, row["relayed_channel_id"])
                link_info = db.fetchone(
                    "SELECT webhook_url FROM linked_channels WHERE channel_id = ?", (cfg_id,)
                )
                if not link_info or not link_info["webhook_url"]:
                    continue
                wh = discord.Webhook.from_url(
                    link_info["webhook_url"],
                    session=self.bot.http._HTTPClient__session,
                )
                await wh.edit_message(
                    int(row["relayed_message_id"]),
                    content=final_content,
                    embeds=payload_embeds,
                    allowed_mentions=discord.AllowedMentions(roles=True, users=True),
                )
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
    async def _relay_to_target(
        self,
        original: Message, source: dict, target: dict, group: dict,
        username: str, avatar_url: str, filtered_content: str,
        exec_id: str, thread_route: dict,
    ):
        reply_embed = None
        reply_ping = ""
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
                if replied.author.id not in [u.id for u in original.mentions]:
                    reply_ping = f"<@{replied.author.id}> "

        # Role mention mapping
        target_content = original.content or ""
        role_mentions = re.findall(r"<@&(\d+)>", target_content)
        if role_mentions:
            target_guild = self.bot.get_guild(int(target["guild_id"]))
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

        payload_content = reply_ping + final_content

        # Attachments — append direct URLs to content (Discord auto-embeds them)
        attachment_urls: list[str] = []
        large_attachments: list[str] = []
        for att in sorted(original.attachments, key=lambda a: a.size):
            line = f"\n{att.url}"
            if len(payload_content) + len(line) + sum(len(u) for u in attachment_urls) <= _DISCORD_MSG_LIMIT - 50:
                attachment_urls.append(line)
            else:
                large_attachments.append(att.filename)
        if attachment_urls:
            payload_content += "".join(attachment_urls)
        if large_attachments:
            payload_content += f"\n*(Note: {len(large_attachments)} file(s) too large: {', '.join(large_attachments)})*"

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

        payload = {
            "content": payload_content,
            "username": username,
            "avatar_url": avatar_url,
            "embeds": [e.to_dict() if hasattr(e, "to_dict") else e for e in payload_embeds],
            "allowed_mentions": {"parse": ["roles", "users"]},
        }
        if original.stickers:
            s = original.stickers[0]
            payload["content"] += f"\n[Sticker: {s.name}]({s.url})"

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
