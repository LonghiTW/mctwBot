"""MCTW Bot entry point with multi-profile token support."""
import asyncio
import sys

import discord
from discord.ext import commands

from bot_profiles import BotProfile, load_bot_profiles, validate_bot_profiles
from config_sync import load_config
from database import DatabaseManager
from utils.log_manager import LogManager
from utils.admin_notifier import notify_admins
from cogs.relay.queue import relay_queue

log = LogManager


def create_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guild_messages = True
    intents.guilds = True
    intents.members = True
    intents.webhooks = True
    if hasattr(intents, "threads"):
        intents.threads = True
    return intents


def create_bot(profile: BotProfile) -> commands.Bot:
    bot = commands.Bot(
        command_prefix=profile.command_prefix,
        intents=create_intents(),
        help_command=None,
    )
    bot.profile = profile
    register_events(bot, profile)
    return bot


def register_events(bot: commands.Bot, profile: BotProfile) -> None:
    @bot.event
    async def on_ready():
        log.info("MAIN", f"[{profile.id}] Bot logged in as {bot.user} (ID: {bot.user.id})")

    @bot.event
    async def on_error(event: str, *args, **kwargs):
        import traceback
        tb = traceback.format_exc()
        log.error("EVENT-ERROR", f"[{profile.id}] Unhandled error in {event}: {tb}")
        try:
            await notify_admins(bot, "⚠️ 未預期錯誤", f"**Bot：** `{profile.id}`\n**事件：** `{event}`\n```{tb[:1500]}```")
        except Exception:
            pass


async def load_extensions(bot: commands.Bot, profile: BotProfile, config: dict) -> None:
    features = profile.features

    if features.get("relay", False):
        await bot.load_extension("cogs.relay.relay_cog")
        log.info("MAIN", f"[{profile.id}] Loaded relay")

    if features.get("keywords", False):
        keywords = config.get("keywords", {})
        if keywords.get("hello", {}).get("enabled", True):
            await bot.load_extension("cogs.keywords.hello")
        if keywords.get("birthday", {}).get("enabled", True):
            await bot.load_extension("cogs.keywords.birthday")
        log.info("MAIN", f"[{profile.id}] Loaded keywords")

    if features.get("scheduler", False):
        scheduler = config.get("scheduler", {})
        if scheduler.get("friday_night", {}).get("enabled", True):
            await bot.load_extension("cogs.scheduler.friday_night")
        if scheduler.get("sunday_night", {}).get("enabled", True):
            await bot.load_extension("cogs.scheduler.sunday_night")
        log.info("MAIN", f"[{profile.id}] Loaded scheduler")

    if features.get("moderation", False):
        moderation = config.get("moderation", {})
        if moderation.get("welcome_cleaner", {}).get("enabled", True):
            await bot.load_extension("cogs.moderation.welcome_cleaner")
        log.info("MAIN", f"[{profile.id}] Loaded moderation")

    if features.get("commands", False):
        await bot.load_extension("cogs.commands.ping")
        log.info("MAIN", f"[{profile.id}] Loaded commands")

    if features.get("admin", False):
        await bot.load_extension("cogs.admin.message_control")
        log.info("MAIN", f"[{profile.id}] Loaded admin")


async def main():
    config = load_config()
    profiles = load_bot_profiles(config)
    validate_bot_profiles(profiles)

    db = DatabaseManager()
    log.info("MAIN", "Database ready")

    bots = [create_bot(profile) for profile in profiles]
    for bot in bots:
        await load_extensions(bot, bot.profile, config)

    await relay_queue.start()
    log.info("MAIN", "Relay queue started.")

    async def shutdown():
        log.info("MAIN", "Shutting down...")
        await relay_queue.stop()
        db.close()
        await asyncio.gather(*(bot.close() for bot in bots), return_exceptions=True)
        LogManager.shutdown()

    try:
        await asyncio.gather(*(bot.start(bot.profile.token) for bot in bots))
    except KeyboardInterrupt:
        await shutdown()
    except Exception as exc:
        log.error("MAIN", f"Fatal: {exc}")
        await asyncio.gather(
            *(notify_admins(bot, "🔥 機器人崩潰", f"```{exc}```") for bot in bots),
            return_exceptions=True,
        )
        await shutdown()
        sys.exit(1)