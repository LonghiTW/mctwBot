"""Build webhook payloads and multipart files for relay sends."""
from collections.abc import Awaitable, Callable

import aiohttp
import discord
from discord import Embed, Message, StickerFormatType

from .rendering import (
    append_attachment_previews,
    format_referenced_message_text,
    resolve_klipy_urls,
    strip_embed_urls_from_content,
)

_DISCORD_MSG_LIMIT = 2000
_NO_MENTIONS = {"parse": []}
_MAX_UPLOAD_BYTES = 8_000_000

EmojiContentResolver = Callable[[str, list, discord.Guild | None], Awaitable[tuple[str, list]]]


class RelayPayloadBuilder:
    def __init__(self, bot: discord.Client, resolve_emojis: EmojiContentResolver):
        self.bot = bot
        self._resolve_emojis = resolve_emojis

    async def build(
        self,
        original: Message,
        target: dict,
        group: dict,
        username: str,
        avatar_url: str,
        content: str,
        reply_embed,
        is_forward: bool,
        exec_id: str,
        thread_route: dict,
    ) -> tuple[dict, dict, list]:
        payload_content = content
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
            description = []
            for index, answer in enumerate(original.poll.answers):
                emoji = answer.emoji or f"{index+1}."
                description.append(f"{emoji} **{answer.text}**")
            poll_embed.description = "\n".join(description)[:4096]
            payload_embeds.append(poll_embed)

        if len(payload_content) > _DISCORD_MSG_LIMIT:
            payload_content = payload_content[:_DISCORD_MSG_LIMIT - 50] + "...(truncated)"

        if reply_embed:
            payload_embeds.append(reply_embed)

        for embed in original.embeds:
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

        payload_content, payload_embeds = await resolve_klipy_urls(payload_content, payload_embeds)
        payload_content = strip_embed_urls_from_content(payload_content, original.embeds)
        target_guild = self.bot.get_guild(int(target["guild_id"]))
        payload_content, payload_embeds = await self._resolve_emojis(payload_content, payload_embeds, target_guild)
        all_attachments = list(original.attachments) + snapshot_attachments
        payload_content, relay_files = append_attachment_previews(payload_content, payload_embeds, all_attachments)

        payload_content, files = await self._download_files_for_upload(payload_content, relay_files)

        if original.stickers:
            attachment_urls = {att.url.rstrip("/") for att in original.attachments}
            payload_content, sticker_files = await self._download_stickers_for_upload(payload_content, original.stickers, attachment_urls)
            files.extend(sticker_files)

        payload = {
            "content": payload_content,
            "username": username,
            "avatar_url": avatar_url,
            "embeds": [embed.to_dict() if hasattr(embed, "to_dict") else embed for embed in payload_embeds],
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
        return payload, meta, files

    async def _download_files_for_upload(self, payload_content: str, relay_files: list) -> tuple[str, list]:
        files_for_upload: list[dict] = []
        if not relay_files:
            return payload_content, files_for_upload

        async with aiohttp.ClientSession() as dl_session:
            for relay_file in relay_files:
                try:
                    async with dl_session.get(
                        relay_file["url"], timeout=aiohttp.ClientTimeout(total=10)
                    ) as response:
                        if response.status == 200:
                            data = await response.read()
                            if len(data) < _MAX_UPLOAD_BYTES:
                                files_for_upload.append({
                                    "filename": relay_file["filename"],
                                    "data": data,
                                    "content_type": relay_file["content_type"],
                                })
                                continue
                except Exception:
                    pass
                clean_url = relay_file["url"].split("?")[0]
                line = f"\n{clean_url}"
                if len(payload_content) + len(line) <= _DISCORD_MSG_LIMIT - 50:
                    payload_content += line
        return payload_content, files_for_upload

    async def _download_stickers_for_upload(self, payload_content: str, stickers, attachment_urls: set[str]) -> tuple[str, list]:
        files_for_upload: list[dict] = []
        async with aiohttp.ClientSession() as session:
            for sticker in stickers:
                sticker_url = sticker.url.rstrip("/")
                if sticker_url in attachment_urls:
                    continue

                upload_item = await self._download_sticker(session, sticker)
                if upload_item:
                    files_for_upload.append(upload_item)
                    continue

                line = f"\n{sticker.url}"
                if len(payload_content) + len(line) <= _DISCORD_MSG_LIMIT - 50:
                    payload_content += line
        return payload_content, files_for_upload

    async def _download_sticker(self, session: aiohttp.ClientSession, sticker) -> dict | None:
        sticker_format = getattr(sticker, "format", None)
        if sticker_format is StickerFormatType.lottie:
            return None

        content_type = _sticker_content_type(sticker_format)
        filename = _sticker_filename(sticker)
        try:
            async with session.get(sticker.url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    return None
                data = await response.read()
                if len(data) >= _MAX_UPLOAD_BYTES:
                    return None
        except Exception:
            return None

        return {
            "filename": filename,
            "data": data,
            "content_type": content_type,
        }


def _sticker_filename(sticker) -> str:
    sticker_name = str(getattr(sticker, "name", "sticker") or "sticker")
    safe_name = "".join(char if char.isalnum() or char in ("_", "-") else "_" for char in sticker_name).strip("_")
    if not safe_name:
        safe_name = "sticker"
    sticker_format = getattr(sticker, "format", None)
    extension = "gif" if sticker_format is StickerFormatType.gif else "png"
    return f"{safe_name[:40]}_{getattr(sticker, 'id', 'unknown')}.{extension}"


def _sticker_content_type(sticker_format) -> str:
    if sticker_format is StickerFormatType.gif:
        return "image/gif"
    if sticker_format is StickerFormatType.apng:
        return "image/apng"
    return "image/png"
