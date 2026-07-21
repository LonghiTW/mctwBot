"""Keyword responder — says hello back."""
import datetime
import random
import re

from discord.ext import commands


class HelloResponder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_trigger_date: dict = {}

        self.zh_keywords = ["你好", "大家好"]
        self.en_keywords = ["hello", "hi"]
        self.en_patterns = [
            re.compile(rf"\b{kw}[.!?~]?\b", re.IGNORECASE)
            for kw in self.en_keywords
        ]
        self.replies_zh = [
            "你好你好！👋",
            "https://i.ytimg.com/vi/XwM4ZRSiXv0/hqdefault.jpg",
        ]
        self.replies_en = ["Hello!", "Hi there! 👋"]

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        today = datetime.date.today()
        cid = message.channel.id
        if self.last_trigger_date.get(cid) == today:
            return

        content = message.content

        if any(kw in content for kw in self.zh_keywords):
            self.last_trigger_date[cid] = today
            await message.channel.send(random.choice(self.replies_zh))
            return

        if any(p.search(content) for p in self.en_patterns):
            self.last_trigger_date[cid] = today
            await message.channel.send(random.choice(self.replies_en))


async def setup(bot):
    await bot.add_cog(HelloResponder(bot))
