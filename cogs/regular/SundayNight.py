from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord import TextChannel
import pytz

class SundayReminder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="Asia/Taipei")

        # ✅ 多個頻道 ID
        self.channel_ids = [
            1349540882369478688,  # CDA
            735692114360533143    # BTW
        ]

        # 每週日 21:00 發送
        self.scheduler.add_job(
            self.send_sunday_image,
            CronTrigger(day_of_week='sun', hour=21, minute=0, timezone=pytz.timezone("Asia/Taipei"))
        )
        self.scheduler.start()

    async def send_sunday_image(self):
        for channel_id in self.channel_ids:
            channel = self.bot.get_channel(channel_id)
            if channel and isinstance(channel, TextChannel):
                try:
                    await channel.send("https://cdn.discordapp.com/attachments/886936474723950611/1396476771377086474/image0.jpg")
                    print(f"✅ 傳送成功：頻道 {channel_id}")
                except Exception as e:
                    print(f"❌ 傳送失敗：頻道 {channel_id}，錯誤：{e}")
            else:
                print(f"❌ 找不到頻道或頻道錯誤：{channel_id}")

    async def cog_unload(self):
        self.scheduler.shutdown(wait=False)

async def setup(bot):
    await bot.add_cog(SundayReminder(bot))
