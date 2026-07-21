"""Scheduled task — Sunday night image at 21:00."""
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord import TextChannel
from discord.ext import commands

from config_sync import load_config


class SundayReminder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="Asia/Taipei")

        self.scheduler.add_job(
            self.send_image,
            CronTrigger(day_of_week="sun", hour=21, minute=0, timezone=pytz.timezone("Asia/Taipei")),
        )
        self.scheduler.start()

    def _get_channels(self) -> list[int]:
        cfg = load_config()
        return cfg.get("scheduler_channels", {}).get("sunday_night", [])

    async def send_image(self):
        for cid in self._get_channels():
            ch = self.bot.get_channel(cid)
            if ch and isinstance(ch, TextChannel):
                try:
                    await ch.send(
                        "https://cdn.discordapp.com/attachments/886936474723950611/"
                        "1396476771377086474/image0.jpg"
                    )
                except Exception as e:
                    print(f"[SundayNight] Send failed {cid}: {e}")

    async def cog_unload(self):
        self.scheduler.shutdown(wait=False)


async def setup(bot):
    await bot.add_cog(SundayReminder(bot))
