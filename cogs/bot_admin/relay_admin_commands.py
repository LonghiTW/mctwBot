"""Slash commands for managing relay groups and channels (bot admins).

Only loaded on bot profiles with the ``relay`` feature. Every command is
restricted to bot_admins with the ``exclusive_command`` feature. Changes are
written to config.json (atomic), validated, synced into the database, and
recorded in the audit log with a DM to ``notifications`` admins.

Structure notes (discord.py 2.7.x):
- Nested subgroups use ``Group`` subclasses (``/relay group add``). The
  subcommand callbacks are bound to the subgroup instance, NOT the cog, so
  shared helpers live at module level and use ``interaction.client`` instead
  of ``self.bot``.
- ``interaction_check`` must be defined on the subgroup class (or referenced
  from it) because discord.py looks it up on the command's ``binding``.
"""
from __future__ import annotations

from typing import Literal, Union

import discord
from discord import app_commands
from discord.ext import commands

from app.bot_admins import bot_admin_has_feature
from app.config_sync import sync_configured_relays
from app.relay_config_editor import (
    RelayConfigEditError,
    add_channel,
    add_group,
    backup_config,
    edit_channel,
    edit_group,
    group_names,
    load_config_file,
    remove_channel,
    remove_group,
    save_config_file,
)
from database import DatabaseManager
from utils.admin_audit import audit_admin_usage
from utils.log_manager import LogManager

log = LogManager

RelayChannel = Union[discord.TextChannel, discord.ForumChannel]
Direction = Literal["BOTH", "SEND_ONLY", "RECEIVE_ONLY"]


class RelayChannelTransformer(app_commands.Transformer):
    """Channel option that works from DMs and across guilds.

    discord.py's default channel transformer resolves through the guild
    cache (``PartialChannel.resolve``), which returns ``None`` when the
    command is invoked from a DM where the guild context is unavailable.
    This transformer falls back to an API ``fetch_channel`` so bot admins
    can reference channels from anywhere the bot is present.
    """

    @property
    def type(self) -> discord.AppCommandOptionType:
        return discord.AppCommandOptionType.channel

    @property
    def channel_types(self) -> list[discord.ChannelType]:
        # Keep the channel picker limited to text-like channels.
        return [discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.forum]

    async def transform(self, interaction: discord.Interaction, value, /) -> RelayChannel:
        resolved = value.resolve()
        if isinstance(resolved, (discord.TextChannel, discord.ForumChannel)):
            return resolved
        try:
            channel = await interaction.client.fetch_channel(value.id)
        except Exception:
            channel = None
        if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            return channel
        raise app_commands.TransformerError(value, discord.AppCommandOptionType.channel, self)

_PERMISSION_DENIED = "❌ 只有 bot_admins 且啟用 exclusive_command 的管理員才能使用此指令。"


# ---------------------------------------------------------------------------
# Shared helpers (module level — subgroup command callbacks are bound to the
# subgroup instance, so they cannot rely on ``self`` pointing at the cog)
# ---------------------------------------------------------------------------
def _channel_kind(channel: discord.abc.GuildChannel) -> str:
    return "text" if isinstance(channel, discord.TextChannel) else "forum"


async def _interaction_check(interaction: discord.Interaction) -> bool:
    """Permission gate shared by every relay slash subcommand."""
    if bot_admin_has_feature(interaction.user.id, "exclusive_command"):
        return True
    await interaction.response.send_message(_PERMISSION_DENIED, ephemeral=True)
    return False


async def _group_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    try:
        config = load_config_file()
        names = group_names(config)
    except Exception:
        names = []
    if current:
        lowered = current.lower()
        names = [name for name in names if lowered in name.lower()]
    return [app_commands.Choice(name=name, value=name) for name in names[:25]]


async def _commit(interaction: discord.Interaction, new_config: dict, action: str, detail: str) -> None:
    """Backup -> atomic save (validates) -> sync DB -> dispatch reload -> audit."""
    client = interaction.client
    backup_config()
    save_config_file(new_config)

    db = DatabaseManager()
    old_rows = db.fetchall(
        """SELECT lc.channel_id, lc.guild_id, lc.group_id, rg.group_name
           FROM linked_channels lc
           JOIN relay_groups rg ON rg.group_id = lc.group_id"""
    )
    await sync_configured_relays(client)
    new_rows = db.fetchall(
        """SELECT lc.channel_id, lc.guild_id, lc.group_id, rg.group_name
           FROM linked_channels lc
           JOIN relay_groups rg ON rg.group_id = lc.group_id"""
    )
    client.dispatch("bot_reload", old_rows, new_rows)

    await audit_admin_usage(client, interaction, action, detail)


async def _run(interaction: discord.Interaction, action: str, detail: str, apply) -> None:
    try:
        config = load_config_file()
        new_config = apply(config)
        await _commit(interaction, new_config, action, detail)
    except RelayConfigEditError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return
    except Exception as exc:
        log.error("SLASH", f"{action} failed: {exc}")
        await interaction.response.send_message(f"❌ 操作失敗：{exc}", ephemeral=True)
        return
    await interaction.response.send_message(f"✅ {action} 完成。", ephemeral=True)


# ---------------------------------------------------------------------------
# /relay group ...
# ---------------------------------------------------------------------------
class RelayGroupSub(app_commands.Group):
    """Subgroup: /relay group ..."""

    interaction_check = staticmethod(_interaction_check)

    @app_commands.command(name="add", description="新增一個 relay group（可先為空）")
    async def add(self, interaction: discord.Interaction, name: str, hidden: bool = False):
        await _run(
            interaction, "relay group add", f"名稱：{name}\n隱藏：{hidden}",
            lambda config: add_group(config, name, hidden=hidden),
        )

    @app_commands.command(name="edit", description="編輯 relay group 名稱或隱藏狀態")
    async def edit(
        self,
        interaction: discord.Interaction,
        group: str,
        new_name: str | None = None,
        hidden: bool | None = None,
    ):
        await _run(
            interaction, "relay group edit",
            f"群組：{group}\n新名稱：{new_name or '（不變）'}\n隱藏：{hidden if hidden is not None else '（不變）'}",
            lambda config: edit_group(config, group, new_name=new_name, hidden=hidden),
        )

    @app_commands.command(name="remove", description="移除 relay group（連同其頻道與角色映射）")
    async def remove(self, interaction: discord.Interaction, group: str):
        await _run(
            interaction, "relay group remove", f"群組：{group}",
            lambda config: remove_group(config, group)[0],
        )

    @edit.autocomplete("group")
    @remove.autocomplete("group")
    async def _autocomplete_group(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await _group_autocomplete(interaction, current)


# ---------------------------------------------------------------------------
# /relay channel ...
# ---------------------------------------------------------------------------
class RelayChannelSub(app_commands.Group):
    """Subgroup: /relay channel ..."""

    interaction_check = staticmethod(_interaction_check)

    @app_commands.command(name="add", description="新增頻道到 relay group")
    async def add(
        self,
        interaction: discord.Interaction,
        group: str,
        channel: app_commands.Transform[RelayChannel, RelayChannelTransformer],
        direction: Direction = "BOTH",
        brand_name: str | None = None,
        process_bot_messages: bool = False,
        allow_forward_delete: bool = True,
        allow_reverse_delete: bool = False,
    ):
        await _run(
            interaction, "relay channel add",
            f"群組：{group}\n頻道：{channel.id} ({channel.name})\n方向：{direction}\n品牌名稱：{brand_name or '（自動）'}\n轉發 bot 訊息：{process_bot_messages}\n順向刪除：{allow_forward_delete}\n反向刪除：{allow_reverse_delete}",
            lambda config: add_channel(
                config, group, channel.id, _channel_kind(channel),
                direction=direction, brand_name=brand_name,
                process_bot_messages=process_bot_messages,
                allow_forward_delete=allow_forward_delete,
                allow_reverse_delete=allow_reverse_delete,
            ),
        )

    @app_commands.command(name="edit", description="編輯 relay 頻道設定（可搬移 group、清空品牌名稱）")
    async def edit(
        self,
        interaction: discord.Interaction,
        channel: app_commands.Transform[RelayChannel, RelayChannelTransformer],
        group: str | None = None,
        direction: Direction | None = None,
        brand_name: str | None = None,
        clear_brand_name: bool = False,
        process_bot_messages: bool | None = None,
        allow_forward_delete: bool | None = None,
        allow_reverse_delete: bool | None = None,
    ):
        await _run(
            interaction, "relay channel edit",
            f"頻道：{channel.id} ({channel.name})\n搬移群組：{group or '（不變）'}\n方向：{direction or '（不變）'}\n品牌名稱：{brand_name or '（不變）'}\n清除品牌名稱：{clear_brand_name}",
            lambda config: edit_channel(
                config, channel.id, _channel_kind(channel),
                group_name=group, direction=direction,
                brand_name=brand_name, clear_brand_name=clear_brand_name,
                process_bot_messages=process_bot_messages,
                allow_forward_delete=allow_forward_delete,
                allow_reverse_delete=allow_reverse_delete,
            )[0],
        )

    @app_commands.command(name="remove", description="從 relay group 移除頻道（保留空 group）")
    async def remove(self, interaction: discord.Interaction, channel: app_commands.Transform[RelayChannel, RelayChannelTransformer]):
        await _run(
            interaction, "relay channel remove",
            f"頻道：{channel.id} ({channel.name})",
            lambda config: remove_channel(config, channel.id)[0],
        )

    @add.autocomplete("group")
    @edit.autocomplete("group")
    async def _autocomplete_group(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await _group_autocomplete(interaction, current)


# ---------------------------------------------------------------------------
# Top-level /relay group
# ---------------------------------------------------------------------------
class RelayCommands(app_commands.Group):
    """Top-level group: /relay"""

    group = RelayGroupSub(name="group", description="管理 relay group")
    channel = RelayChannelSub(name="channel", description="管理 relay group 內的頻道")


class RelayAdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    relay = RelayCommands(name="relay", description="管理 relay 群組與頻道（bot 管理員）")


async def setup(bot: commands.Bot):
    await bot.add_cog(RelayAdminCommands(bot))
