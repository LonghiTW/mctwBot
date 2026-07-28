"""Helpers for webhook message operations in relayed channels."""
import discord

from database import DatabaseManager
from .routing import configured_channel_id_for_stored_channel, webhook_thread_for_stored_channel


class WebhookMessageClient:
    def __init__(self, bot: discord.Client):
        self.bot = bot

    def from_stored_channel(self, db: DatabaseManager, stored_channel_id: str):
        cfg_id = configured_channel_id_for_stored_channel(db, stored_channel_id)
        link = db.fetchone(
            "SELECT webhook_url FROM linked_channels WHERE channel_id = ?",
            (cfg_id,),
        )
        if not link or not link["webhook_url"]:
            return None, None
        webhook = discord.Webhook.from_url(
            link["webhook_url"],
            session=self.bot.http._HTTPClient__session,
        )
        thread = webhook_thread_for_stored_channel(db, stored_channel_id)
        return webhook, thread

    async def delete_message(self, db: DatabaseManager, stored_channel_id: str, message_id: str) -> None:
        webhook, thread = self.from_stored_channel(db, stored_channel_id)
        if webhook is None:
            raise LookupError(f"Missing webhook for relayed channel {stored_channel_id}")
        if thread:
            await webhook.delete_message(int(message_id), thread=thread)
        else:
            await webhook.delete_message(int(message_id))

    async def fetch_message(self, db: DatabaseManager, stored_channel_id: str, message_id: str):
        webhook, _thread = self.from_stored_channel(db, stored_channel_id)
        if webhook is None:
            raise LookupError(f"Missing webhook for relayed channel {stored_channel_id}")
        return await webhook.fetch_message(int(message_id))

    async def edit_message(self, db: DatabaseManager, stored_channel_id: str, message_id: str, **kwargs) -> None:
        webhook, thread = self.from_stored_channel(db, stored_channel_id)
        if webhook is None:
            raise LookupError(f"Missing webhook for relayed channel {stored_channel_id}")
        if thread:
            kwargs["thread"] = thread
        await webhook.edit_message(int(message_id), **kwargs)
