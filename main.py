"""MCTW Bot entry point with multi-profile token support."""
import asyncio
import sys

import discord
from discord.ext import commands

from bot_profiles import BotProfile, load_bot_profiles, validate_bot_profiles
from config_validator import validate_config
from config_sync import load_config
from database import DatabaseManager
from utils.log_manager import LogManager
from utils.admin_notifier import notify_admins
from cogs.relay.queue import relay_queue

log = LogManager

FEATURE_EXTENSIONS = {
    "relay": ("cogs.relay.relay_cog",),
    "commands": ("cogs.commands.ping",),
    "admin": ("cogs.admin.message_control",),
}

CONFIGURED_FEATURE_EXTENSIONS = {
    "keywords": {
        "hello": "cogs.keywords.hello",
        "birthday": "cogs.keywords.birthday",
    },
    "scheduler": {
        "friday_night": "cogs.scheduler.friday_night",
        "sunday_night": "cogs.scheduler.sunday_night",
    },
    "moderation": {
        "welcome_cleaner": "cogs.moderation.welcome_cleaner",
    },
}


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

    for feature, extensions in FEATURE_EXTENSIONS.items():
        if features.get(feature, False):
            for extension in extensions:
                await bot.load_extension(extension)
            log.info("MAIN", f"[{profile.id}] Loaded {feature}")

    for feature, extensions in CONFIGURED_FEATURE_EXTENSIONS.items():
        if not features.get(feature, False):
            continue
        feature_config = config.get(feature, {})
        for name, extension in extensions.items():
            if feature_config.get(name, {}).get("enabled", True):
                await bot.load_extension(extension)
        log.info("MAIN", f"[{profile.id}] Loaded {feature}")


async def main():
    config = load_config()
    validate_config(config)
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