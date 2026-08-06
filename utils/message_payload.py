"""Shared helpers for parsing/rendering JSON message payloads.

Used by the ``!msg`` (guild_admin) and ``!announce`` (bot_admin) commands so
both accept the same JSON shape and build discord objects consistently.
"""
from __future__ import annotations

import json

import discord


def message_from_json(payload: str) -> dict:
    """Parse a JSON message payload string into kwargs for ``channel.send``."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("Message JSON must be an object.")

    embeds = data.get("embeds", [])
    if not isinstance(embeds, list):
        raise ValueError("Message embeds must be an array.")

    message = {
        "content": data.get("content"),
        "embeds": [embed_from_dict(item) for item in embeds],
    }
    if message["content"] is None and not message["embeds"]:
        raise ValueError("Message JSON must include content or at least one embed.")
    if data.get("allowed_mentions") == "none":
        message["allowed_mentions"] = discord.AllowedMentions.none()
    return message


def embed_from_dict(data: dict) -> discord.Embed:
    if not isinstance(data, dict):
        raise ValueError("Each embed must be an object.")

    color = data.get("color")
    if isinstance(color, str):
        color = int(color.removeprefix("#"), 16)
    elif color is not None:
        color = int(color)

    embed = discord.Embed(
        title=data.get("title"),
        description=data.get("description"),
        url=data.get("url"),
        color=color,
    )

    author = data.get("author")
    if isinstance(author, dict):
        embed.set_author(
            name=author.get("name"),
            url=author.get("url"),
            icon_url=author.get("icon_url"),
        )

    footer = data.get("footer")
    if isinstance(footer, str):
        embed.set_footer(text=footer)
    elif isinstance(footer, dict):
        embed.set_footer(text=footer.get("text"), icon_url=footer.get("icon_url"))

    image = data.get("image")
    if isinstance(image, str):
        embed.set_image(url=image)
    elif isinstance(image, dict):
        embed.set_image(url=image.get("url"))

    thumbnail = data.get("thumbnail")
    if isinstance(thumbnail, str):
        embed.set_thumbnail(url=thumbnail)
    elif isinstance(thumbnail, dict):
        embed.set_thumbnail(url=thumbnail.get("url"))

    fields = data.get("fields", [])
    if not isinstance(fields, list):
        raise ValueError("Embed fields must be an array.")
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("Each embed field must be an object.")
        embed.add_field(
            name=str(field.get("name", ""))[:256],
            value=str(field.get("value", ""))[:1024],
            inline=bool(field.get("inline", False)),
        )
    return embed


def message_to_json(message: discord.Message) -> dict:
    return {
        "content": message.content or None,
        "embeds": [embed.to_dict() for embed in message.embeds],
    }
