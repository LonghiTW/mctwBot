"""Relay reload notification formatting.

``!relaylist`` moved to ``cogs/guild_admin/relaylist.py`` (Discord guild
admin permission). This module keeps the pieces still used by the reload
flow: welcome/update notifications and channel link formatting.
"""
import re

import discord

_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0001F1E6-\U0001F1FF"
    "\u200d\ufe0f"
    "]+"
)


def _strip_emoji(value: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_RE.sub("", value)).strip()


def format_channel_link(bot: discord.Client, guild_id: str, channel_id: str) -> str:
    guild_name = str(guild_id)

    try:
        guild = bot.get_guild(int(guild_id))
        if guild:
            guild_name = guild.name
        else:
            channel = bot.get_channel(int(channel_id))
            if channel and getattr(channel, "guild", None):
                guild_name = channel.guild.name
    except Exception:
        pass

    guild_name = _strip_emoji(guild_name) or str(guild_id)
    return f"[{guild_name}](https://discord.com/channels/{guild_id}/{channel_id})"


class RelayAdminViews:
    def __init__(self, bot: discord.Client):
        self.bot = bot

    def format_channel_link(self, guild_id: str, channel_id: str) -> str:
        return format_channel_link(self.bot, guild_id, channel_id)

    def build_reload_notifications(self, old_rows: list[dict], new_rows: list[dict]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        old_by_group: dict[str, set[str]] = {}
        for row in old_rows:
            old_by_group.setdefault(row["group_name"], set()).add(row["channel_id"])

        new_by_group: dict[str, set[str]] = {}
        for row in new_rows:
            new_by_group.setdefault(row["group_name"], set()).add(row["channel_id"])

        channel_guild: dict[str, str] = {}
        for row in old_rows + new_rows:
            channel_guild[row["channel_id"]] = row["guild_id"]

        update_messages: list[tuple[str, str]] = []
        welcome_messages: list[tuple[str, str]] = []
        all_group_names = set(old_by_group) | set(new_by_group)

        for group_name in sorted(all_group_names):
            old_set = old_by_group.get(group_name, set())
            new_set = new_by_group.get(group_name, set())

            added = new_set - old_set
            removed = old_set - new_set
            kept = old_set & new_set

            if not added and not removed:
                continue

            msg_parts = [f"**{group_name} 頻道更新**"]
            for channel_id in sorted(added):
                guild_id = channel_guild.get(channel_id, "?")
                msg_parts.append(f"  ➕ 新增 {self.format_channel_link(guild_id, channel_id)}")
            for channel_id in sorted(removed):
                guild_id = channel_guild.get(channel_id, "?")
                msg_parts.append(f"  ➖ 移除 {self.format_channel_link(guild_id, channel_id)}")
            notify_text = "\n".join(msg_parts)

            for channel_id in kept:
                update_messages.append((channel_id, notify_text))

            for channel_id in sorted(added):
                others = [
                    self.format_channel_link(channel_guild.get(other_id, "?"), other_id)
                    for other_id in sorted(new_set) if other_id != channel_id
                ]
                other_text = "\n".join(f"- {other}" for other in others) if others else "無"
                welcome = (
                    f"👋 此頻道已加入麥塊聯盟的群組 **{group_name}**。\n"
                    f"群組內的其他頻道：\n{other_text}"
                )
                welcome_messages.append((channel_id, welcome))

        return update_messages, welcome_messages
