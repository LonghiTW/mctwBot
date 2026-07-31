"""Reaction synchronization for relayed Discord messages."""
from collections.abc import Awaitable, Callable

import discord

from database import DatabaseManager
from utils.log_manager import LogManager

log = LogManager

EmojiResolver = Callable[[discord.PartialEmoji, discord.Guild | None], Awaitable[discord.PartialEmoji | str]]


class ReactionSync:
    def __init__(self, bot: discord.Client, resolve_emoji: EmojiResolver):
        self.bot = bot
        self._resolve_emoji = resolve_emoji

    async def sync(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        """Find all copies of a relayed message and mirror the reaction."""
        db = DatabaseManager()
        message_id = str(payload.message_id)
        channel_id = str(payload.channel_id)

        copies = db.fetchall(
            "SELECT relayed_message_id, relayed_channel_id FROM relayed_messages WHERE original_message_id = ?",
            (message_id,),
        )
        original = db.fetchone(
            "SELECT original_message_id, original_channel_id FROM relayed_messages WHERE relayed_message_id = ?",
            (message_id,),
        )

        if not copies and not original:
            return

        targets: set[tuple[str, str]] = set()
        for row in copies:
            targets.add((row["relayed_message_id"], row["relayed_channel_id"]))

        if original:
            # The reaction landed on a relayed copy — fan out to the original
            # and every sibling copy so the whole group stays in sync.
            root_id = original["original_message_id"]
            targets.add((original["original_message_id"], original["original_channel_id"]))
            siblings = db.fetchall(
                "SELECT relayed_message_id, relayed_channel_id FROM relayed_messages WHERE original_message_id = ?",
                (root_id,),
            )
            for row in siblings:
                targets.add((row["relayed_message_id"], row["relayed_channel_id"]))
        targets.discard((message_id, channel_id))

        if not targets:
            return

        emoji = payload.emoji
        for target_message_id, target_channel_id in targets:
            channel = self.bot.get_channel(int(target_channel_id))
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(int(target_channel_id))
                except Exception:
                    continue
            try:
                target_guild = getattr(channel, "guild", None)
                resolved = str(emoji) if emoji.id is None else await self._resolve_emoji(emoji, target_guild)
                if resolved is None:
                    continue
                message = await channel.fetch_message(int(target_message_id))
                if add:
                    await message.add_reaction(resolved)
                else:
                    if self.bot.user is None:
                        return
                    await message.remove_reaction(resolved, discord.Object(id=self.bot.user.id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                log.warn(
                    "REACTION-SYNC",
                    f"Failed to {'add' if add else 'remove'} reaction on {target_message_id}: {exc}",
                )
