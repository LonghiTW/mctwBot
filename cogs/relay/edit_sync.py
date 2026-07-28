"""Message edit synchronization for relayed Discord messages."""
import re
from collections.abc import Awaitable, Callable

import discord
from discord import Embed, Message

from database import DatabaseManager
from utils.log_manager import LogManager
from .routing import configured_channel_id_for_stored_channel, linked_channel_id_for_message, webhook_thread_for_stored_channel
from .rendering import append_attachment_previews, resolve_klipy_urls, strip_embed_urls_from_content

log = LogManager

_MAX_USERNAME_LENGTH = 80
_DISCORD_MSG_LIMIT = 2000

EmojiContentResolver = Callable[[str, list, discord.Guild | None], Awaitable[tuple[str, list]]]


class EditSync:
    def __init__(self, bot: discord.Client, resolve_emojis: EmojiContentResolver):
        self.bot = bot
        self._resolve_emojis = resolve_emojis

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
        final_content, payload_embeds = await self._resolve_emojis(final_content, payload_embeds, None)
        final_content, _ = append_attachment_previews(final_content, payload_embeds, message.attachments)

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
                webhook = discord.Webhook.from_url(
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
                await webhook.edit_message(int(row["relayed_message_id"]), **edit_kwargs)
            except discord.NotFound:
                pass
            except Exception as exc:
                log.error("EDIT", f"Failed {row['relayed_message_id']}: {exc}")
