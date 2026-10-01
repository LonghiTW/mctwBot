"""Privacy policy command — available to any profile with the commands feature."""
from discord import app_commands
from discord.ext import commands

from app.config import PRIVACY_POLICY_URL


class Privacy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="privacy", description="查看 MCTW Bot 隱私權政策")
    async def privacy(self, interaction):
        if PRIVACY_POLICY_URL:
            response = f"MCTW Bot 隱私權政策：<{PRIVACY_POLICY_URL}>"
        else:
            response = "隱私權政策連結尚未設定，請聯絡此 Bot 的管理員。"
        await interaction.response.send_message(response, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Privacy(bot))
