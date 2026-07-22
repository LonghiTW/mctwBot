"""Welcome message cleaner — auto-deletes welcome messages when members leave.

Bug fixed: original used `if not message.author.bot` which filtered out
Discord's system welcome messages (sent by system, not bot). Now uses
`message.type == discord.MessageType.new_member` to correctly detect them.
"""
import discord
from discord.ext import commands

from config_sync import load_config


class WelcomeCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # (guild_id, user_id) → {message_id, channel_id}
        self.welcome_messages: dict = {}

    def _get_channels(self) -> set[int]:
        cfg = load_config()
        moderation = cfg.get("moderation", {})
        welcome_cleaner = moderation.get("welcome_cleaner", {})
        if "channels" in welcome_cleaner:
            return set(welcome_cleaner.get("channels", []))
        return set(cfg.get("welcome_channels", []))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ✅ Bug fix: use message.type, not message.author.bot
        if message.type != discord.MessageType.new_member:
            return
        if message.channel.id not in self._get_channels():
            return
        if len(message.mentions) != 1:
            return

        member = message.mentions[0]
        key = (message.guild.id, member.id)
        self.welcome_messages[key] = {
            "message_id": message.id,
            "channel_id": message.channel.id,
        }

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        key = (member.guild.id, member.id)
        record = self.welcome_messages.get(key)
        if not record:
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
