"""Admin-only JSON message control commands."""
from __future__ import annotations

import json
from io import BytesIO

import discord
from discord.ext import commands

from app.config_sync import load_config
from database import DatabaseManager


class MessageControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        admin_ids = {int(uid) for uid in load_config().get("admin", {}).get("user_ids", [])}
        if ctx.author.id in admin_ids:
            return True
        if isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator:
            return True
        await ctx.send("You are not allowed to use message control commands.")
        return False

    @commands.group(invoke_without_command=True)
    async def msg(self, ctx: commands.Context):
        await ctx.send("Usage: `!msg send`, `!msg edit`, `!msg delete`, `!msg source`")

    @commands.command(name="announce")
    async def announce(self, ctx: commands.Context, group_name: str, *, payload: str):
        data = self._message_from_json(payload)
        channels = self._group_channel_ids(group_name)
        if not channels:
            await ctx.send(f"Relay group not found or has no channels: `{group_name}`")
            return

        sent = 0
        skipped = 0
        failed: list[str] = []
        for channel_id in channels:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(int(channel_id))
                except discord.DiscordException as exc:
                    failed.append(f"{channel_id}: {exc}")
                    continue

            if not isinstance(channel, discord.TextChannel):
                skipped += 1
                continue

            try:
                await channel.send(**data)
                sent += 1
            except discord.DiscordException as exc:
                failed.append(f"{channel_id}: {exc}")

        summary = f"Announcement sent to {sent} channel(s); skipped {skipped} non-text channel(s)."
        if failed:
            details = "\n".join(failed[:5])
            summary += f"\nFailed {len(failed)} channel(s):\n```\n{details}\n```"
        await ctx.send(summary)

    @msg.command(name="send")
    async def msg_send(self, ctx: commands.Context, channel: discord.TextChannel, *, payload: str):
        data = self._message_from_json(payload)
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
        data = self._message_from_json(payload)
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
        await message.delete()
        await ctx.send(f"Deleted message: `{message.id}`")

    @msg.command(name="source")
    async def msg_source(self, ctx: commands.Context, message_id: int):
        message = await self._find_message(ctx, message_id)
        if not message:
            await ctx.send("Message not found.")
            return
        payload = self._message_to_json(message)
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

    def _group_channel_ids(self, group_name: str) -> list[str]:
        db = DatabaseManager()
        rows = db.fetchall(
            """SELECT lc.channel_id
               FROM linked_channels lc
               JOIN relay_groups rg ON rg.group_id = lc.group_id
               WHERE rg.group_name = ?
               ORDER BY lc.channel_id""",
            (group_name,),
        )
        return [str(row["channel_id"]) for row in rows]

    def _message_from_json(self, payload: str) -> dict:
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
            "embeds": [self._embed_from_dict(item) for item in embeds],
        }
        if message["content"] is None and not message["embeds"]:
            raise ValueError("Message JSON must include content or at least one embed.")
        if data.get("allowed_mentions") == "none":
            message["allowed_mentions"] = discord.AllowedMentions.none()
        return message

    def _embed_from_dict(self, data: dict) -> discord.Embed:
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

    def _message_to_json(self, message: discord.Message) -> dict:
        return {
            "content": message.content or None,
            "embeds": [embed.to_dict() for embed in message.embeds],
        }


async def setup(bot):
    await bot.add_cog(MessageControl(bot))