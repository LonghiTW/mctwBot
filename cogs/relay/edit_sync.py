"""Message edit synchronization for relayed Discord messages."""
import re
from collections.abc import Awaitable, Callable

import discord
from discord import Embed, Message

from database import DatabaseManager
from utils.log_manager import LogManager
from .routing import linked_channel_id_for_message
from .rendering import (
    append_attachment_previews,
    build_reply_embed,
    resolve_klipy_urls,
    strip_embed_urls_from_content,
)
from .webhook_messages import WebhookMessageClient
from .message_store import RelayMessageStore

log = LogManager

_MAX_USERNAME_LENGTH = 80
_DISCORD_MSG_LIMIT = 2000

EmojiContentResolver = Callable[[str, list, discord.Guild | None], Awaitable[tuple[str, list]]]


class EditSync:
    def __init__(self, bot: discord.Client, resolve_emojis: EmojiContentResolver):
        self.bot = bot
        self._resolve_emojis = resolve_emojis
        self.webhooks = WebhookMessageClient(bot)

    async def sync_edit(self, message: Message, relayed_message_types) -> None:
        if not message.guild:
            return
        if message.type not in relayed_message_types:
            return
        if self.bot.user and message.author.id == self.bot.user.id:
            return
        if message.webhook_id and message.application_id == self.bot.user.id:
            return

        db = DatabaseManager()
        store = RelayMessageStore(db)
        if not store.has_original(str(message.id)):
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
            for row in filters:
                final_content = re.sub(rf"\b{re.escape(row['phrase'])}\b", "***", final_content, flags=re.IGNORECASE)

        sender_name = message.author.display_name
        server_brand = source["brand_name"] or message.guild.name
        username = f"{sender_name} ({server_brand})"
        if len(username) > _MAX_USERNAME_LENGTH:
            username = username[:_MAX_USERNAME_LENGTH - 3] + "..."

        if len(final_content) > _DISCORD_MSG_LIMIT:
            final_content = final_content[:_DISCORD_MSG_LIMIT - 50] + "...(truncated)"

        payload_embeds = []
        for embed in message.embeds:
            clean = Embed(
                title=embed.title,
                description=embed.description[:4096] if embed.description else None,
                color=embed.color, url=embed.url, timestamp=embed.timestamp,
            )
            if embed.author:
                clean.set_author(name=embed.author.name, url=embed.author.url, icon_url=embed.author.icon_url)
            if embed.footer:
                clean.set_footer(text=embed.footer.text, icon_url=embed.footer.icon_url)
            if embed.image:
                clean.set_image(url=embed.image.url)
            if embed.thumbnail:
                clean.set_thumbnail(url=embed.thumbnail.url)
            if embed.fields:
                for field in embed.fields:
                    clean.add_field(name=field.name, value=field.value, inline=field.inline)
            payload_embeds.append(clean)
        final_content, payload_embeds = await resolve_klipy_urls(final_content, payload_embeds)
        final_content = strip_embed_urls_from_content(final_content, message.embeds)
        final_content, _ = append_attachment_previews(final_content, payload_embeds, message.attachments)

        # Reply reconstruction — fetch the referenced message once (mirrors _relay_to_target)
        replied_message: Message | None = None
        reply_deleted = False
        if message.reference and message.reference.message_id:
            try:
                replied_message = await message.channel.fetch_message(message.reference.message_id)
            except Exception:
                replied_message = None
            if replied_message is None and isinstance(message.channel, discord.Thread) and message.channel.parent:
                try:
                    replied_message = await message.channel.parent.fetch_message(message.reference.message_id)
                except Exception:
                    replied_message = None
            if replied_message is None:
                reply_deleted = True

        relayed = store.relayed_for_original(str(message.id))
        for row in relayed:
            try:
                target_channel = self.bot.get_channel(int(row["relayed_channel_id"]))
                if target_channel is None:
                    try:
                        target_channel = await self.bot.fetch_channel(int(row["relayed_channel_id"]))
                    except Exception:
                        target_channel = None
                target_guild = getattr(target_channel, "guild", None) if target_channel else None
                edit_content, edit_embeds = await self._resolve_emojis(final_content, list(payload_embeds), target_guild)

                # Keep the reply embed in place (first embed) — otherwise edits wipe it out
                if message.reference and message.reference.message_id:
                    if reply_deleted:
                        reply_embed = Embed(color=0xB0B8C6, description="*Replying to a deleted message.*")
                    else:
                        parent_rec = db.fetchone(
                            "SELECT original_message_id FROM relayed_messages WHERE relayed_message_id = ?",
                            (str(replied_message.id),),
                        )
                        root_id = parent_rec["original_message_id"] if parent_rec else str(replied_message.id)
                        copy = db.fetchone(
                            """SELECT relayed_message_id FROM relayed_messages
                               WHERE original_message_id = ? AND relayed_channel_id = ?""",
                            (root_id, row["relayed_channel_id"]),
                        )
                        guild_id = getattr(target_guild, "id", None) or "0"
                        link = (
                            f"https://discord.com/channels/{guild_id}/{row['relayed_channel_id']}/{copy['relayed_message_id']}"
                            if copy else str(replied_message.jump_url)
                        )
                        reply_embed = build_reply_embed(replied_message, link, deleted=False)
                    edit_embeds = [reply_embed] + edit_embeds

                edit_kwargs = {
                    "content": edit_content,
                    "embeds": edit_embeds,
                    "allowed_mentions": discord.AllowedMentions.none(),
                }
                await self.webhooks.edit_message(db, row["relayed_channel_id"], row["relayed_message_id"], **edit_kwargs)
            except LookupError:
                pass
            except discord.NotFound:
                pass
            except Exception as exc:
                log.error("EDIT", f"Failed {row['relayed_message_id']}: {exc}")
