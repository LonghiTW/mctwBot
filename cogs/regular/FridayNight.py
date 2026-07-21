from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timedelta
from astral.sun import sun
from astral import LocationInfo
import pytz

class FridayNight(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
        self.tz = pytz.timezone("Asia/Taipei")
        self.channel_ids = [
            1349540882369478688,  # CDA
            735692114360533143    # BTW
        ]
        self.city = LocationInfo("Taipei", "Taiwan", "Asia/Taipei", 25.0330, 121.5654)

        self.schedule_next_friday()
        self.scheduler.start()

    def schedule_next_friday(self):
        today = datetime.now(self.tz).date()
        days_ahead = (4 - today.weekday()) % 7  # 星期五 = 4
        next_friday = today + timedelta(days=days_ahead)

        s = sun(self.city.observer, date=next_friday, tzinfo=self.tz)
        target_time = s["sunset"] + timedelta(minutes=1)

        # 若今天是週五且時間已過，排下週
        if target_time < datetime.now(self.tz):
            next_friday += timedelta(days=7)
            s = sun(self.city.observer, date=next_friday, tzinfo=self.tz)
            target_time = s["sunset"] + timedelta(minutes=1)

        self.scheduler.add_job(self.send_gif, trigger=DateTrigger(run_date=target_time, timezone=self.tz))
        print(f"✅ 已排程在 {target_time.strftime('%Y-%m-%d %H:%M:%S')} 傳送 gif")

    async def send_gif(self):
        for channel_id in self.channel_ids:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.send("https://cdn.discordapp.com/attachments/1298824829633564714/1400393437366194246/image0.gif")
                    print(f"✅ 成功傳送 gif 至頻道 {channel_id}")
                except Exception as e:
                    print(f"❌ 傳送到 {channel_id} 失敗：{e}")
            else:
                print(f"❌ 找不到頻道 {channel_id}")

        # 安排下週
        self.schedule_next_friday()

    async def cog_unload(self):
        self.scheduler.shutdown(wait=False)

async def setup(bot):
    await bot.add_cog(FridayNight(bot))
