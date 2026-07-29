"""Admin-facing relay list and reload notification formatting."""
import re

import discord

from app.config_sync import load_config
from database import DatabaseManager

_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0001F1E6-\U0001F1FF"
    "\u200d\ufe0f"
    "]+"
)


class RelayAdminViews:
    def __init__(self, bot: discord.Client):
        self.bot = bot

    def is_config_admin(self, member: discord.User | discord.Member | None) -> bool:
        if member is None:
            return False
        admin_ids = {int(uid) for uid in load_config().get("admin", {}).get("user_ids", [])}
        return member.id in admin_ids

    def can_view_relaylist(self, author: discord.User | discord.Member | None) -> bool:
        if author is None:
            return False
        if self.is_config_admin(author):
            return True
        if isinstance(author, discord.Member) and author.guild:
            return author.guild_permissions.manage_guild or author.guild_permissions.administrator
        return False

    def format_channel_link(self, guild_id: str, channel_id: str) -> str:
        guild_name = str(guild_id)

        try:
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                guild_name = guild.name
            else:
                channel = self.bot.get_channel(int(channel_id))
                if channel and getattr(channel, "guild", None):
                    guild_name = channel.guild.name
        except Exception:
            pass

        guild_name = _strip_emoji(guild_name) or str(guild_id)
        return f"[{guild_name}](https://discord.com/channels/{guild_id}/{channel_id})"

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
                other_text = "、".join(others) if others else "無"
                welcome = (
                    f"👋 此頻道已加入麥塊聯盟的群組 **{group_name}**。\n"
                    f"群組內其他頻道：{other_text}"
                )
                welcome_messages.append((channel_id, welcome))

        return update_messages, welcome_messages

    def relaylist_chunks(self) -> list[str]:
        hidden_groups = self._hidden_groups()

        db = DatabaseManager()
        groups = db.fetchall("SELECT * FROM relay_groups ORDER BY group_name")
        if not groups:
            return []

        lines: list[str] = []
        for group in groups:
            if group["group_name"] in hidden_groups:
                continue

            channels = db.fetchall(
                "SELECT * FROM linked_channels WHERE group_id = ? ORDER BY guild_id, channel_id",
                (group["group_id"],),
            )
            lines.append(f"**{group['group_name']}**")

            if not channels:
                lines.append("  └ *無頻道*")
                continue

            for index, channel in enumerate(channels):
                prefix = "  └" if index == len(channels) - 1 else "  ├"
                direction = "🔄" if channel["direction"] == "BOTH" else ("📤" if channel["direction"] == "SEND_ONLY" else "📥")
                lines.append(f"{prefix} {direction} {self.format_channel_link(channel['guild_id'], channel['channel_id'])}")

            lines.append("")

        text = "\n".join(lines).strip()
        if not text:
            return ["目前沒有顯示任何中繼群組。"]
        return self._chunk_text(text)

    def _hidden_groups(self) -> set[str]:
        config = load_config()
        relay_cfg = config.get("relay", {})
        hidden_groups: set[str] = set()
        for group_config in relay_cfg.get("groups", []):
            if group_config.get("hidden", False):
                hidden_groups.add(str(group_config.get("name", "")).strip())
        return hidden_groups

    def _chunk_text(self, text: str) -> list[str]:
        if len(text) <= 1900:
            return [text]

        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks


def _strip_emoji(value: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_RE.sub("", value)).strip()
