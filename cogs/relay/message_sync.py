"""Delete synchronization for relayed Discord messages."""
import asyncio

import discord

from database import DatabaseManager
from utils.log_manager import LogManager
from .queue import relay_queue
from .routing import configured_channel_id_for_stored_channel
from .rendering import build_reply_embed
from .webhook_messages import WebhookMessageClient
from .message_store import RelayMessageStore

log = LogManager


class MessageSync:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._recently_deleted: set[str] = set()
        self.webhooks = WebhookMessageClient(bot)

    async def sync_reverse_delete(self, relayed_message_id: str) -> bool:
        db = DatabaseManager()
        store = RelayMessageStore(db)
        link = store.original_for_relayed(relayed_message_id)
        if not link:
            return False
        orig_cfg = configured_channel_id_for_stored_channel(db, link["original_channel_id"])
        src = db.fetchone(
            "SELECT allow_reverse_delete FROM linked_channels WHERE channel_id = ?",
            (orig_cfg,),
        )
        if not src or not src["allow_reverse_delete"]:
            return True
        try:
            channel = await self.bot.fetch_channel(int(link["original_channel_id"]))
            original = await channel.fetch_message(int(link["original_message_id"]))
            await original.delete()
        except Exception:
            pass
        return True

    async def sync_forward_delete(self, original_message_id: str, channel_id: str) -> bool:
        if original_message_id in self._recently_deleted:
            return True
        self._recently_deleted.add(original_message_id)
        asyncio.get_running_loop().call_later(5, self._recently_deleted.discard, original_message_id)

        relay_queue.cancel(original_message_id)

        db = DatabaseManager()
        store = RelayMessageStore(db)
        await self.mark_replied_message_deleted(db, original_message_id)

        src = db.fetchone(
            "SELECT allow_forward_delete FROM linked_channels WHERE channel_id = ?",
            (configured_channel_id_for_stored_channel(db, channel_id),),
        )
        if not src or not src["allow_forward_delete"]:
            return False

        relayed = store.relayed_for_original(original_message_id)
        if not relayed:
            return False

        deleted = 0
        failed = 0
        for row in relayed:
            relayed_message_id = str(row["relayed_message_id"])
            try:
                await self.webhooks.delete_message(db, row["relayed_channel_id"], relayed_message_id)
                deleted += 1
                store.delete_mapping(original_message_id, relayed_message_id)
            except LookupError as exc:
                failed += 1
                log.warn("DEL-FWD", str(exc))
            except discord.NotFound:
                deleted += 1
                store.delete_mapping(original_message_id, relayed_message_id)
            except Exception as exc:
                failed += 1
                log.warn("DEL-FWD", f"Delete failed {relayed_message_id} in {row['relayed_channel_id']}: {exc}")

        log.info("DEL-FWD", f"Deleted {deleted}/{len(relayed)} relayed copies for {original_message_id}; failed={failed}")
        return True

    async def mark_replied_message_deleted(self, db: DatabaseManager, original_message_id: str) -> None:
        store = RelayMessageStore(db)
        replies = store.replies_to_original(original_message_id)
        if not replies:
            return

        deleted_embed = build_reply_embed(None, deleted=True)
        for row in replies:
            try:
                message = await self.webhooks.fetch_message(db, row["relayed_channel_id"], row["relayed_message_id"])
                embeds = list(message.embeds)
                if embeds:
                    embeds[0] = deleted_embed
                else:
                    embeds = [deleted_embed]
                kwargs = {"embeds": embeds, "allowed_mentions": discord.AllowedMentions.none()}
                await self.webhooks.edit_message(db, row["relayed_channel_id"], row["relayed_message_id"], **kwargs)
            except LookupError:
                pass
            except discord.NotFound:
                pass
            except Exception as exc:
                log.warn("REPLY-DEL", f"Failed to update reply embed {row['relayed_message_id']}: {exc}")

