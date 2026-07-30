"""Custom emoji resolution and cache-guild upload helpers for relay."""
import asyncio
import re

import aiohttp
import discord

from app.config_sync import load_config
from database import DatabaseManager
from utils.log_manager import LogManager

log = LogManager

_CUSTOM_EMOJI_RE = re.compile(r'<(a?):(\w+):(\d+)>')
_CACHE_PREFIX_RE = re.compile(r'^relay_.+_\d{6}$')
_CACHE_EMOJI_HEADROOM = 5
_DISCORD_MAX_EMOJIS_REACHED = 30008


class EmojiResolver:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._locks: dict[str, asyncio.Lock] = {}

    async def sync_cache_index(self) -> None:
        """Reconcile relay emoji cache DB records with the cache guild on startup."""
        config = load_config()
        cache_guild_id = config.get("relay", {}).get("emoji_cache_guild_id", "")
        if not cache_guild_id:
            return

        cache_guild = self.bot.get_guild(int(cache_guild_id))
        if cache_guild is None:
            try:
                cache_guild = await self.bot.fetch_guild(int(cache_guild_id))
            except Exception as exc:
                log.warn("EMOJI-CACHE", f"Failed to fetch cache guild {cache_guild_id}: {exc}")
                return

        db = DatabaseManager()
        rows = db.fetchall(
            """SELECT source_emoji_id, cached_emoji_id, cached_name
               FROM relay_emoji_cache
               WHERE cache_guild_id = ?""",
            (cache_guild_id,),
        )
        emoji_by_id = {str(emoji.id): emoji for emoji in cache_guild.emojis}

        removed_rows = 0
        active_cached_ids: set[str] = set()
        for row in rows:
            cached_id = str(row["cached_emoji_id"])
            if cached_id in emoji_by_id:
                active_cached_ids.add(cached_id)
                continue
            db.execute("DELETE FROM relay_emoji_cache WHERE source_emoji_id = ?", (row["source_emoji_id"],))
            removed_rows += 1
        if removed_rows:
            db.commit()

        orphan_deleted = 0
        can_manage = bool(
            getattr(cache_guild, "me", None)
            and cache_guild.me.guild_permissions.manage_expressions
        )
        if can_manage:
            for emoji in list(cache_guild.emojis):
                emoji_id = str(emoji.id)
                if emoji_id in active_cached_ids:
                    continue
                if not _CACHE_PREFIX_RE.match(emoji.name):
                    continue
                try:
                    await emoji.delete(reason="Relay emoji cache orphan cleanup")
                    orphan_deleted += 1
                except Exception as exc:
                    log.warn("EMOJI-CACHE", f"Failed to delete orphan relay emoji {emoji.name} ({emoji.id}): {exc}")

        log.info(
            "EMOJI-CACHE",
            f"Synced cache index for {cache_guild_id}: removed_rows={removed_rows}, orphan_deleted={orphan_deleted}",
        )

    async def ensure_cached(self, source_emoji_id: str, animated: bool, source_name: str) -> str | None:
        """Upload an external emoji to the cache guild and return the cached emoji id, or None on failure."""
        lock = self._locks.setdefault(source_emoji_id, asyncio.Lock())
        async with lock:
            return await self._ensure_cached_locked(source_emoji_id, animated, source_name)

    async def _ensure_cached_locked(self, source_emoji_id: str, animated: bool, source_name: str) -> str | None:
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
        cached_id = self._cached_id_from_db(db, cache_guild, source_emoji_id)
        if cached_id:
            return cached_id

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

        cache_name = _cache_name(source_name, source_emoji_id)
        existing = discord.utils.get(cache_guild.emojis, name=cache_name)
        if existing:
            self._save_cache_mapping(db, source_emoji_id, cache_guild_id, existing, cache_name, animated, cdn_url)
            log.info("EMOJI-CACHE", f"Adopted existing {cache_name} ({existing.id}) in {cache_guild_id}")
            return str(existing.id)

        await self._evict_until_headroom(db, cache_guild, animated)

        try:
            new_emoji = await cache_guild.create_custom_emoji(
                name=cache_name,
                image=image_bytes,
                reason="Relay emoji cache",
            )
        except discord.HTTPException as exc:
            if getattr(exc, "code", None) == _DISCORD_MAX_EMOJIS_REACHED:
                if await self._evict_one(db, cache_guild, animated):
                    try:
                        new_emoji = await cache_guild.create_custom_emoji(
                            name=cache_name,
                            image=image_bytes,
                            reason="Relay emoji cache after eviction",
                        )
                    except Exception as retry_exc:
                        log.warn("EMOJI-CACHE", f"Failed to create emoji {cache_name} after eviction: {retry_exc}")
                        return None
                else:
                    log.warn("EMOJI-CACHE", f"Emoji cache full and no relay emoji could be evicted for {cache_name}: {exc}")
                    return None
            else:
                log.warn("EMOJI-CACHE", f"Failed to create emoji {cache_name}: {exc}")
                return None
        except Exception as exc:
            log.warn("EMOJI-CACHE", f"Failed to create emoji {cache_name}: {exc}")
            return None

        self._save_cache_mapping(db, source_emoji_id, cache_guild_id, new_emoji, cache_name, animated, cdn_url)
        log.info("EMOJI-CACHE", f"Cached {source_emoji_id} as {cache_name} ({new_emoji.id}) in {cache_guild_id}")
        return str(new_emoji.id)

    def _cached_id_from_db(self, db: DatabaseManager, cache_guild: discord.Guild, source_emoji_id: str) -> str | None:
        row = db.fetchone(
            "SELECT cached_emoji_id FROM relay_emoji_cache WHERE source_emoji_id = ?",
            (source_emoji_id,),
        )
        if not row:
            return None

        cached_id = str(row["cached_emoji_id"])
        if cached_id not in {str(emoji.id) for emoji in cache_guild.emojis}:
            db.execute("DELETE FROM relay_emoji_cache WHERE source_emoji_id = ?", (source_emoji_id,))
            db.commit()
            log.warn("EMOJI-CACHE", f"Removed stale cache record for {source_emoji_id} -> {cached_id}")
            return None

        db.execute(
            "UPDATE relay_emoji_cache SET last_used_at = datetime('now'), use_count = use_count + 1 WHERE source_emoji_id = ?",
            (source_emoji_id,),
        )
        db.commit()
        return cached_id

    def _save_cache_mapping(self, db: DatabaseManager, source_emoji_id: str, cache_guild_id: str, emoji, cache_name: str, animated: bool, cdn_url: str) -> None:
        db.execute(
            """INSERT INTO relay_emoji_cache
               (source_emoji_id, cache_guild_id, cached_emoji_id, cached_name,
                animated, source_url, created_at, last_used_at, use_count)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), 1)
               ON CONFLICT(source_emoji_id) DO UPDATE SET
                   cache_guild_id = excluded.cache_guild_id,
                   cached_emoji_id = excluded.cached_emoji_id,
                   cached_name = excluded.cached_name,
                   animated = excluded.animated,
                   source_url = excluded.source_url,
                   last_used_at = datetime('now'),
                   use_count = relay_emoji_cache.use_count + 1""",
            (
                source_emoji_id,
                cache_guild_id,
                str(emoji.id),
                cache_name,
                1 if animated else 0,
                cdn_url,
            ),
        )
        db.commit()

    async def _evict_until_headroom(self, db: DatabaseManager, cache_guild: discord.Guild, animated: bool) -> None:
        while _emoji_slots_remaining(cache_guild, animated) <= _CACHE_EMOJI_HEADROOM:
            if not await self._evict_one(db, cache_guild, animated):
                return

    async def _evict_one(self, db: DatabaseManager, cache_guild: discord.Guild, animated: bool) -> bool:
        rows = db.fetchall(
            """SELECT source_emoji_id, cached_emoji_id, cached_name
               FROM relay_emoji_cache
               WHERE cache_guild_id = ? AND animated = ?
               ORDER BY use_count ASC, last_used_at ASC
               LIMIT 20""",
            (str(cache_guild.id), 1 if animated else 0),
        )
        for row in rows:
            emoji = discord.utils.get(cache_guild.emojis, id=int(row["cached_emoji_id"]))
            if emoji is None:
                db.execute("DELETE FROM relay_emoji_cache WHERE source_emoji_id = ?", (row["source_emoji_id"],))
                db.commit()
                return True
            if not _CACHE_PREFIX_RE.match(row["cached_name"]):
                continue
            try:
                await emoji.delete(reason="Relay emoji cache eviction")
            except Exception as exc:
                log.warn("EMOJI-CACHE", f"Failed to evict {row['cached_name']} ({row['cached_emoji_id']}): {exc}")
                continue
            db.execute("DELETE FROM relay_emoji_cache WHERE source_emoji_id = ?", (row["source_emoji_id"],))
            db.commit()
            log.info("EMOJI-CACHE", f"Evicted {row['cached_name']} ({row['cached_emoji_id']})")
            return True
        return False

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

            if self._is_cache_guild_emoji(emoji_id):
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

    def _is_cache_guild_emoji(self, emoji_id: str) -> bool:
        config = load_config()
        cache_guild_id = config.get("relay", {}).get("emoji_cache_guild_id", "")
        if not cache_guild_id:
            return False
        cache_guild = self.bot.get_guild(int(cache_guild_id))
        if cache_guild is None:
            return False
        return emoji_id in {str(emoji.id) for emoji in cache_guild.emojis}

    async def resolve_reaction(self, emoji: discord.PartialEmoji) -> discord.PartialEmoji | str:
        """Resolve a custom reaction emoji via the cache guild when needed."""
        if emoji.id is None:
            return str(emoji)

        cached_id = await self.ensure_cached(str(emoji.id), emoji.animated, emoji.name)
        if cached_id:
            return discord.PartialEmoji(name=emoji.name, animated=emoji.animated, id=int(cached_id))

        return emoji


def _cache_name(source_name: str, source_emoji_id: str) -> str:
    safe_name = re.sub(r'[^a-z0-9_]', '_', source_name.lower()).strip("_") or "emoji"
    suffix = source_emoji_id[-6:]
    if _CACHE_PREFIX_RE.match(safe_name) and safe_name.endswith(f"_{suffix}"):
        return safe_name[:32]
    if _CACHE_PREFIX_RE.match(safe_name):
        safe_name = safe_name[:-7]
    if safe_name.endswith(f"_{suffix}"):
        safe_name = safe_name[:-(len(suffix) + 1)]
    if safe_name.startswith("relay_"):
        safe_name = safe_name[6:]
    cache_name = f"relay_{safe_name}_{suffix}"
    return cache_name[:32]


def _emoji_slots_remaining(cache_guild: discord.Guild, animated: bool) -> int:
    limit = getattr(cache_guild, "emoji_limit", 50)
    used = sum(1 for emoji in cache_guild.emojis if bool(getattr(emoji, "animated", False)) == animated)
    return max(0, limit - used)
