from discord.ext import commands
import discord

class WelcomeCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # (guild_id, user_id) → {message_id, channel_id}
        self.welcome_messages = {}

        # 歡迎頻道
        self.welcome_channels = [
            1015827632731996251,  # Server A
            955118110493532200    # Server B
        ]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        # 只處理系統歡迎訊息
        if not message.author.bot:
            return

        # 檢查頻道
        if message.channel.id not in self.welcome_channels:
            return

        # 檢查 mention 人數
        if len(message.mentions) != 1:
            return

        member = message.mentions[0]

        # 設定 key：guild + user
        key = (message.guild.id, member.id)

        # 記錄訊息 ID 與頻道 ID
        self.welcome_messages[key] = {
            "message_id": message.id,
            "channel_id": message.channel.id
        }


    @commands.Cog.listener()
    async def on_member_remove(self, member):

        key = (member.guild.id, member.id)

        record = self.welcome_messages.get(key)
        if not record:
            return

        channel_id = record["channel_id"]
        message_id = record["message_id"]

        channel = member.guild.get_channel(channel_id)
        if not channel:
            return

        try:
            msg = await channel.fetch_message(message_id)
            await msg.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            pass

        # 清理紀錄
        self.welcome_messages.pop(key, None)


async def setup(bot):
    await bot.add_cog(WelcomeCleaner(bot))
