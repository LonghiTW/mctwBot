"""Keyword responder — birthday wishes."""
import datetime
import random

from discord.ext import commands


class BirthdayResponder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_trigger_date: dict = {}
        self.keywords = ["生日", "birthday", "hbd"]
        self.replies = [
            "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSFqfCfC1u-9ZNfWQi4MR3AJGWaRFggbOQwK5yX0BNUk2kbcSnecnd_nN4&s=10",
            "https://megapx-assets.dcard.tw/images/be86cb9c-92d3-4a47-8ee8-c3421bd74579/orig.jpeg",
            "https://media.discordapp.net/attachments/1298824829633564714/1440952723708051507/image0.gif",
        ]

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.guild is None:
            return

        today = datetime.date.today()
        gid = message.guild.id
        if self.last_trigger_date.get(gid) == today:
            return

        if any(kw in message.content for kw in self.keywords):
            self.last_trigger_date[gid] = today
            await message.channel.send(random.choice(self.replies))


async def setup(bot):
    await bot.add_cog(BirthdayResponder(bot))
