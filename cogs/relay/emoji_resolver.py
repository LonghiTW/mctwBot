"""Custom emoji resolution and cache-guild upload helpers for relay."""
import re

import aiohttp
import discord

from app.config_sync import load_config
from database import DatabaseManager
from utils.log_manager import LogManager

log = LogManager

_CUSTOM_EMOJI_RE = re.compile(r'<(a?):(\w+):(\d+)>')


class EmojiResolver:
    def __init__(self, bot: discord.Client):
        self.bot = bot

    async def ensure_cached(self, source_emoji_id: str, animated: bool, source_name: str) -> str | None:
        """Upload an external emoji to the cache guild and return the cached emoji id, or None on failure."""
        config = load_config()
        cache_guild_id = config.get("relay", {}).get("emoji_cache_guild_id", "")
        if not cache_guild_id:
            return None

        cache_guild = self.bot.get_guild(int(cache_guild_id))
        if cache_guild is None:
            try:
                cache_guild = await self.bot.fetch_guild(int(cache_guild_id))
            except Exception:
                return None

        if not cache_guild.me or not cache_guild.me.guild_permissions.manage_expressions:
            log.warn("EMOJI-CACHE", f"No Manage Expressions in cache guild {cache_guild_id}")
            return None

        db = DatabaseManager()
        row = db.fetchone(
            "SELECT cached_emoji_id FROM relay_emoji_cache WHERE source_emoji_id = ?",
            (source_emoji_id,),
        )
        if row:
            db.execute(
                "UPDATE relay_emoji_cache SET last_used_at = datetime('now'), use_count = use_count + 1 WHERE source_emoji_id = ?",
                (source_emoji_id,),
            )
            db.commit()
            return row["cached_emoji_id"]

        ext = "gif" if animated else "png"
        cdn_url = f"https://cdn.discordapp.com/emojis/{source_emoji_id}.{ext}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(cdn_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    image_bytes = await resp.read()
        except Exception:
            return None

        safe_name = re.sub(r'[^a-z0-9_]', '_', source_name.lower())
        cache_name = f"relay_{safe_name}_{source_emoji_id[-6:]}"
        if len(cache_name) > 32:
            cache_name = cache_name[:32]

        try:
            new_emoji = await cache_guild.create_custom_emoji(
                name=cache_name,
                image=image_bytes,
                reason="Relay emoji cache",
            )
        except Exception as exc:
            log.warn("EMOJI-CACHE", f"Failed to create emoji {cache_name}: {exc}")
            return None

        db.execute(
            """INSERT INTO relay_emoji_cache
               (source_emoji_id, cache_guild_id, cached_emoji_id, cached_name,
                animated, source_url, created_at, last_used_at, use_count)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), 1)""",
            (
                source_emoji_id,
                cache_guild_id,
                str(new_emoji.id),
                cache_name,
                1 if animated else 0,
                cdn_url,
            ),
        )
        db.commit()
        log.info("EMOJI-CACHE", f"Cached {source_emoji_id} as {cache_name} ({new_emoji.id}) in {cache_guild_id}")
        return str(new_emoji.id)

    async def resolve_content(self, content: str, embeds: list, target_guild: discord.Guild | None = None) -> tuple[str, list]:
        """Resolve custom emoji in message content for a target guild."""
        matches = list(_CUSTOM_EMOJI_RE.finditer(content))
        if not matches:
            return content, embeds

        if target_guild is None:
            return content, embeds

        guild_emoji_ids = {str(emoji.id) for emoji in target_guild.emojis}
        replacements: list[tuple[int, int, str]] = []
        for match in matches:
            animated = match.group(1) == "a"
            name = match.group(2)
            emoji_id = match.group(3)

            if emoji_id in guild_emoji_ids:
                continue

            cached_id = await self.ensure_cached(emoji_id, animated, name)
            if cached_id:
                new_code = f"<a:{name}:{cached_id}>" if animated else f"<:{name}:{cached_id}>"
                replacements.append((match.start(), match.end(), new_code))

        if replacements:
            replacements.sort(key=lambda item: item[0], reverse=True)
            for start, end, new_text in replacements:
                content = content[:start] + new_text + content[end:]

        return content, embeds

    async def resolve_reaction(self, emoji: discord.PartialEmoji) -> discord.PartialEmoji | str:
        """Resolve a custom reaction emoji via the cache guild when needed."""
        if emoji.id is None:
            return str(emoji)

        cached_id = await self.ensure_cached(str(emoji.id), emoji.animated, emoji.name)
        if cached_id:
            return discord.PartialEmoji(name=emoji.name, animated=emoji.animated, id=int(cached_id))

        return emoji
