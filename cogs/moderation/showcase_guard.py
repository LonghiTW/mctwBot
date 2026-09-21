"""Showcase channel guard — keeps showcase channels clean.

Pure-text messages are not allowed in showcase channels; posts must
carry an image (attachment, embed image/thumbnail) or a video link.
Applicable areas are configured in config.json via `areas`, where each
entry is either a channel id (digits) or a relay group name, which is
resolved through the database to its member channels.

Discussion inside threads of a showcase channel is always allowed.
"""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from app.config_sync import load_config
from database import DatabaseManager

DEFAULT_HINT = (
    "本頻道僅供作品展示，若想討論請開啟討論串或移駕其他頻道。"
    "本提示將於 15 秒後刪除。"
)


class ShowcaseGuard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---- configuration -------------------------------------------------

    @staticmethod
    def _module_config() -> dict:
        """Showcase settings live in config.json (global), not per-guild config."""
        moderation = load_config().get("moderation", {})
        showcase = moderation.get("showcase", {})
        return showcase if isinstance(showcase, dict) else {}

    @classmethod
    def _enabled(cls) -> bool:
        return bool(cls._module_config().get("enabled", False))

    @classmethod
    def _configured_channels(cls) -> set[int]:
        """Resolve `areas` entries to channel ids.

        Each area is either a channel id (digits) or a relay group name,
        which is looked up in the database for its member channels.
        """
        raw_areas = cls._module_config().get("areas", [])
        if not isinstance(raw_areas, list):
            return set()

        channel_ids: set[int] = set()
        group_names: list[str] = []
        for area in raw_areas:
            value = str(area).strip()
            if value.isdigit():
                channel_ids.add(int(value))
            elif value:
                group_names.append(value)

        if group_names:
            db = DatabaseManager()
            placeholders = ", ".join("?" for _ in group_names)
            rows = db.fetchall(
                f"""SELECT lc.channel_id
                    FROM relay_groups rg
                    JOIN linked_channels lc ON lc.group_id = rg.group_id
                    WHERE rg.group_name IN ({placeholders})""",
                tuple(group_names),
            )
            for row in rows:
                try:
                    channel_ids.add(int(row["channel_id"]))
                except (TypeError, ValueError):
                    continue
        return channel_ids

    # ---- helpers -------------------------------------------------------

    # Video link patterns accepted as showcase content (e.g. YouTube links).
    VIDEO_LINK_HOSTS = ("youtube.com", "youtu.be")

    @classmethod
    def _has_showcase_content(cls, message: discord.Message) -> bool:
        if any(attachment.content_type and attachment.content_type.startswith("image/")
               for attachment in message.attachments):
            return True
        for embed in message.embeds:
            if embed.image or embed.thumbnail or embed.video:
                return True
            if cls._is_video_link(embed.url):
                return True
            if cls._is_video_link(embed.author and embed.author.url):
                return True
        if message.content:
            from urllib.parse import urlparse
            for word in message.content.split():
                if cls._is_video_link(word.strip("<>")):
                    return True
        return False

    @classmethod
    def _is_video_link(cls, url: object) -> bool:
        if not url or not isinstance(url, str):
            return False
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in cls.VIDEO_LINK_HOSTS)

    def _is_exempt(self, member: discord.Member, module_cfg: dict) -> bool:
        if member.guild_permissions.manage_messages:
            return True
        bypass_roles = module_cfg.get("bypass_roles", [])
        if not isinstance(bypass_roles, list):
            return False
        bypass_ids = {str(role_id).strip() for role_id in bypass_roles}
        return any(str(role.id) in bypass_ids for role in getattr(member, "roles", []))

    # ---- events ---------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        # Threads inside a showcase channel are for discussion — always allow.
        channel = message.channel
        if isinstance(channel, discord.Thread):
            return
        if not isinstance(channel, discord.TextChannel):
            return

        if not self._enabled():
            return
        if channel.id not in self._configured_channels():
            return

        module_cfg = self._module_config()
        if isinstance(message.author, discord.Member) and self._is_exempt(message.author, module_cfg):
            return

        if self._has_showcase_content(message):
            return

        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            return

        hint = str(module_cfg.get("hint", "")).strip() or DEFAULT_HINT
        try:
            await channel.send(
                content=f"{message.author.mention} {hint}",
                delete_after=15,
            )
        except (discord.Forbidden, discord.HTTPException):
            return


async def setup(bot):
    await bot.add_cog(ShowcaseGuard(bot))
