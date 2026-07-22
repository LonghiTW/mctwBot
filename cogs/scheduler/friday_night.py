"""Scheduled task — Friday night GIF at sunset."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from astral.sun import sun
from astral import LocationInfo
from discord import TextChannel
from discord.ext import commands

from config_sync import load_config


class FridayNight(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tz = ZoneInfo("Asia/Taipei")
        self.scheduler = AsyncIOScheduler(timezone=str(self.tz))
        self.city = LocationInfo("Taipei", "Taiwan", "Asia/Taipei", 25.0330, 121.5654)
        self.schedule_next_friday()
        self.scheduler.start()

    def _get_channels(self) -> list[int]:
        cfg = load_config()
        friday_night = cfg.get("scheduler", {}).get("friday_night", {})
        if "channels" in friday_night:
            return friday_night.get("channels", [])
        return cfg.get("scheduler_channels", {}).get("friday_night", [])

    def schedule_next_friday(self):
        today = datetime.now(self.tz).date()
        days_ahead = (4 - today.weekday()) % 7
        next_friday = today + timedelta(days=days_ahead)

        s = sun(self.city.observer, date=next_friday, tzinfo=self.tz)
        target = s["sunset"] + timedelta(minutes=1)

        if target < datetime.now(self.tz):
            next_friday += timedelta(days=7)
            s = sun(self.city.observer, date=next_friday, tzinfo=self.tz)
            target = s["sunset"] + timedelta(minutes=1)

        self.scheduler.add_job(
            self.send_gif,
            trigger=DateTrigger(run_date=target, timezone=self.tz),
        )
        print(f"[FridayNight] Scheduled at {target.strftime('%Y-%m-%d %H:%M:%S')}")

    async def send_gif(self):
        for cid in self._get_channels():
            ch = self.bot.get_channel(cid)
            if ch and isinstance(ch, TextChannel):
                try:
                    await ch.send(
                        "https://cdn.discordapp.com/attachments/1298824829633564714/"
                        "1400393437366194246/image0.gif"
                    )
                except Exception as e:
                    print(f"[FridayNight] Send failed {cid}: {e}")
            else:
                print(f"[FridayNight] Channel {cid} not found")
        self.schedule_next_friday()

    async def cog_unload(self):
        self.scheduler.shutdown(wait=False)


async def setup(bot):
    await bot.add_cog(FridayNight(bot))
