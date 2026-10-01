"""Fetch the bot's own avatar for use as a webhook avatar."""
from __future__ import annotations

import discord

from utils.log_manager import LogManager

log = LogManager


async def fetch_bot_avatar(client: discord.Client) -> bytes | None:
    """Return the bot user's display avatar as PNG/JPEG bytes, or None.

    Webhook messages already override the per-message name/avatar, but the
    webhook's own profile page uses the webhook avatar. Setting it to the bot
    avatar keeps that page intentional instead of showing Discord's default.
    """
    user = getattr(client, "user", None)
    if user is None:
        return None
    asset = getattr(user, "display_avatar", None)
    if asset is None:
        return None
    try:
        return await asset.read()
    except Exception as exc:
        log.warn("WEBHOOK", f"Failed to read bot avatar: {exc}")
        return None


async def ensure_webhook_avatar(client: discord.Client, webhook_url: str) -> None:
    """Set an existing webhook's avatar to the bot avatar when it differs."""
    avatar = await fetch_bot_avatar(client)
    if not avatar:
        return
    try:
        webhook = discord.Webhook.from_url(webhook_url, client=client)
        await webhook.edit(avatar=avatar, reason="Relay webhook avatar")
    except Exception as exc:
        log.warn("WEBHOOK", f"Failed to update webhook avatar: {exc}")

