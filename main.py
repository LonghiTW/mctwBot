"""MCTW Bot — entry point."""
import asyncio
import sys

import discord
from discord.ext import commands

from config import validate, DISCORD_TOKEN
from config_sync import load_config
from database import DatabaseManager
from utils.log_manager import LogManager
from utils.admin_notifier import notify_admins
from cogs.relay.queue import relay_queue

log = LogManager

intents = discord.Intents.default()
intents.message_content = True
intents.guild_messages = True
intents.guilds = True
intents.members = True
intents.webhooks = True
if hasattr(intents, "threads"):
    intents.threads = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    log.info("MAIN", f"Bot logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_error(event: str, *args, **kwargs):
    import traceback
    tb = traceback.format_exc()
    log.error("EVENT-ERROR", f"Unhandled error in {event}: {tb}")
    try:
        await notify_admins(bot, "⚠️ 未預期錯誤", f"**事件：** `{event}`\n```{tb[:1500]}```")
    except Exception:
        pass


async def main():
    validate()

    config = load_config()
    db = DatabaseManager()
    log.info("MAIN", f"Database ready")

    # Load relay cog (always)
    await bot.load_extension("cogs.relay.relay_cog")

    # Conditionally load optional cogs
    features = config.get("features", {})

    if features.get("keyword_responder", False):
        await bot.load_extension("cogs.keywords.hello")
        await bot.load_extension("cogs.keywords.birthday")
        log.info("MAIN", "Loaded keyword_responder")

    if features.get("scheduler", False):
        await bot.load_extension("cogs.scheduler.friday_night")
        await bot.load_extension("cogs.scheduler.sunday_night")
        log.info("MAIN", "Loaded scheduler")

    if features.get("welcome_cleaner", False):
        await bot.load_extension("cogs.moderation.welcome_cleaner")
        log.info("MAIN", "Loaded welcome_cleaner")

    if features.get("ping_command", False):
        await bot.load_extension("cogs.commands.ping")
        log.info("MAIN", "Loaded ping_command")

    # Start relay queue
    await relay_queue.start()
    log.info("MAIN", "Relay queue started.")

    # Graceful shutdown
    async def shutdown():
        log.info("MAIN", "Shutting down...")
        await relay_queue.stop()
        db.close()
        await bot.close()

    try:
        await bot.start(DISCORD_TOKEN)
    except KeyboardInterrupt:
        await shutdown()
    except Exception as exc:
        log.error("MAIN", f"Fatal: {exc}")
        try:
            await notify_admins(bot, "🔥 機器人崩潰", f"```{exc}```")
        except Exception:
            pass
        await shutdown()
        sys.exit(1)
