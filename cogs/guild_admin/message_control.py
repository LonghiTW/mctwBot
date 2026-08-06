"""Discord guild-admin JSON message control commands.

Restricted to members with ``manage_guild`` or ``administrator`` in the
guild where the command is used. ``!msg`` can only target channels inside
the command's own guild — cross-guild messaging belongs to ``!announce``
(bot admin feature). Every usage is logged and DMed to bot admins with the
``notifications`` feature.
"""
from __future__ import annotations

import json
from io import BytesIO

import discord
from discord.ext import commands

from utils.admin_audit import audit_admin_usage
from utils.message_payload import message_from_json, message_to_json


class MessageControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        if isinstance(ctx.author, discord.Member) and ctx.author.guild:
            if ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator:
                return True
        await ctx.send("❌ 只有擁有「管理伺服器」權限的成員才能使用 message control 指令。")
        return False

    @commands.group(invoke_without_command=True)
    async def msg(self, ctx: commands.Context):
        await ctx.send("Usage: `!msg send`, `!msg edit`, `!msg delete`, `!msg source`")

    @msg.command(name="send")
    async def msg_send(self, ctx: commands.Context, channel: discord.TextChannel, *, payload: str):
        if ctx.guild is None or channel.guild.id != ctx.guild.id:
            await ctx.send("❌ 只能傳送到此 Discord 伺服器內的頻道。")
            return
        data = message_from_json(payload)
        await audit_admin_usage(
            self.bot, ctx, "msg send",
            f"頻道：{channel.id} ({channel.name})\n內容：{payload}",
        )
        message = await channel.send(**data)
        await ctx.send(f"Sent message: `{message.id}`")

    @msg.command(name="edit")
    async def msg_edit(self, ctx: commands.Context, message_id: int, *, payload: str):
        message = await self._find_message(ctx, message_id)
        if not message:
            await ctx.send("Message not found.")
            return
        if message.author.id != self.bot.user.id:
            await ctx.send("Can only edit messages sent by this bot.")
            return
        data = message_from_json(payload)
        await audit_admin_usage(
            self.bot, ctx, "msg edit",
            f"訊息：{message_id}\n內容：{payload}",
        )
        await message.edit(**data)
        await ctx.send(f"Edited message: `{message.id}`")

    @msg.command(name="delete")
    async def msg_delete(self, ctx: commands.Context, message_id: int):
        message = await self._find_message(ctx, message_id)
        if not message:
            await ctx.send("Message not found.")
            return
        if message.author.id != self.bot.user.id:
            await ctx.send("Can only delete messages sent by this bot.")
            return
        await audit_admin_usage(self.bot, ctx, "msg delete", f"訊息：{message_id}")
        await message.delete()
        await ctx.send(f"Deleted message: `{message.id}`")

    @msg.command(name="source")
    async def msg_source(self, ctx: commands.Context, message_id: int):
        message = await self._find_message(ctx, message_id)
        if not message:
            await ctx.send("Message not found.")
            return
        payload = message_to_json(message)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if len(text) <= 1900:
            await ctx.send(f"```json\n{text}\n```")
            return
        file = discord.File(BytesIO(text.encode("utf-8")), filename="message.json")
        await ctx.send("Message JSON is too large to display in Discord.", file=file)

    @msg_send.error
    @msg_edit.error
    async def msg_json_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandInvokeError) and isinstance(error.original, ValueError):
            await ctx.send(str(error.original))
            return
        raise error

    async def _find_message(self, ctx: commands.Context, message_id: int) -> discord.Message | None:
        try:
            return await ctx.channel.fetch_message(message_id)
        except discord.NotFound:
            pass

        if not ctx.guild:
            return None
        for channel in ctx.guild.text_channels:
            try:
                return await channel.fetch_message(message_id)
            except discord.NotFound:
                continue
            except discord.Forbidden:
                continue
        return None


async def setup(bot):
    await bot.add_cog(MessageControl(bot))
