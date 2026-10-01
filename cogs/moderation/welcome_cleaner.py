"""Welcome message cleaner — auto-deletes welcome messages when members leave.

Bug fixed: original used `if not message.author.bot` which filtered out
Discord's system welcome messages (sent by system, not bot). Now uses
`message.type == discord.MessageType.new_member` to correctly detect them.
"""
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from app.guild_config import guild_configs

_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


class WelcomeCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # (guild_id, user_id) → {message_id, channel_id}
        self.welcome_messages: dict = {}

    def _get_channels(self, guild_id: int) -> set[int]:
        return set(guild_configs.channels_for(guild_id, "moderation", "welcome_cleaner"))

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._prune_cache.is_running():
            self._prune_cache.start()

    def cog_unload(self):
        self._prune_cache.cancel()

    @tasks.loop(hours=1)
    async def _prune_cache(self):
        self._remove_expired_records()

    def _remove_expired_records(self, now: int | None = None) -> int:
        now = now or int(datetime.now(timezone.utc).timestamp())
        cutoff = now - _CACHE_TTL_SECONDS
        expired = [
            key for key, record in self.welcome_messages.items()
            if record.get("created_at", 0) <= cutoff
        ]
        for key in expired:
            self.welcome_messages.pop(key, None)
        return len(expired)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        self._remove_expired_records()
        # ✅ Bug fix: use message.type, not message.author.bot
        if message.type != discord.MessageType.new_member:
            return
        if message.guild is None:
            return
        if message.channel.id not in self._get_channels(message.guild.id):
            return
        if len(message.mentions) != 1:
            return

        member = message.mentions[0]
        key = (message.guild.id, member.id)
        self.welcome_messages[key] = {
            "message_id": message.id,
            "channel_id": message.channel.id,
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        key = (member.guild.id, member.id)
        record = self.welcome_messages.get(key)
        if not record:
            return
        if int(datetime.now(timezone.utc).timestamp()) - record.get("created_at", 0) >= _CACHE_TTL_SECONDS:
            self.welcome_messages.pop(key, None)
            return

        channel = member.guild.get_channel(record["channel_id"])
        if not channel:
            return

        try:
            msg = await channel.fetch_message(record["message_id"])
            await msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        self.welcome_messages.pop(key, None)


async def setup(bot):
    await bot.add_cog(WelcomeCleaner(bot))
