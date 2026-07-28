"""Delete synchronization for relayed Discord messages."""
import asyncio

import discord

from database import DatabaseManager
from utils.log_manager import LogManager
from .queue import relay_queue
from .routing import configured_channel_id_for_stored_channel, webhook_thread_for_stored_channel
from .rendering import build_reply_embed

log = LogManager


class MessageSync:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._recently_deleted: set[str] = set()

    async def sync_reverse_delete(self, relayed_message_id: str) -> bool:
        db = DatabaseManager()
        link = db.fetchone(
            """SELECT original_message_id, original_channel_id
               FROM relayed_messages WHERE relayed_message_id = ?""",
            (relayed_message_id,),
        )
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
        await self.mark_replied_message_deleted(db, original_message_id)

        src = db.fetchone(
            "SELECT allow_forward_delete FROM linked_channels WHERE channel_id = ?",
            (configured_channel_id_for_stored_channel(db, channel_id),),
        )
        if not src or not src["allow_forward_delete"]:
            return False

        relayed = db.fetchall(
            "SELECT relayed_message_id, relayed_channel_id FROM relayed_messages WHERE original_message_id = ?",
            (original_message_id,),
        )
        if not relayed:
            return False

        deleted = 0
        failed = 0
        for row in relayed:
            relayed_message_id = str(row["relayed_message_id"])
            try:
                cfg_id = configured_channel_id_for_stored_channel(db, row["relayed_channel_id"])
                link = db.fetchone(
                    "SELECT webhook_url FROM linked_channels WHERE channel_id = ?", (cfg_id,)
                )
                if not link or not link["webhook_url"]:
                    failed += 1
                    log.warn("DEL-FWD", f"Missing webhook for relayed channel {row['relayed_channel_id']} (cfg {cfg_id})")
                    continue
                webhook = discord.Webhook.from_url(
                    link["webhook_url"],
                    session=self.bot.http._HTTPClient__session,
                )
                thread = webhook_thread_for_stored_channel(db, row["relayed_channel_id"])
                if thread:
                    await webhook.delete_message(int(relayed_message_id), thread=thread)
                else:
                    await webhook.delete_message(int(relayed_message_id))
                deleted += 1
                self.delete_relay_record(db, original_message_id, relayed_message_id)
            except discord.NotFound:
                deleted += 1
                self.delete_relay_record(db, original_message_id, relayed_message_id)
            except Exception as exc:
                failed += 1
                log.warn("DEL-FWD", f"Delete failed {relayed_message_id} in {row['relayed_channel_id']}: {exc}")

        log.info("DEL-FWD", f"Deleted {deleted}/{len(relayed)} relayed copies for {original_message_id}; failed={failed}")
        return True

    async def mark_replied_message_deleted(self, db: DatabaseManager, original_message_id: str) -> None:
        replies = db.fetchall(
            """SELECT relayed_message_id, relayed_channel_id
               FROM relayed_messages
               WHERE replied_to_id = ?""",
            (original_message_id,),
        )
        if not replies:
            return

        deleted_embed = build_reply_embed(None, deleted=True)
        for row in replies:
            try:
                cfg_id = configured_channel_id_for_stored_channel(db, row["relayed_channel_id"])
                link = db.fetchone(
                    "SELECT webhook_url FROM linked_channels WHERE channel_id = ?",
                    (cfg_id,),
                )
                if not link or not link["webhook_url"]:
                    continue
                webhook = discord.Webhook.from_url(
                    link["webhook_url"],
                    session=self.bot.http._HTTPClient__session,
                )
                message = await webhook.fetch_message(int(row["relayed_message_id"]))
                embeds = list(message.embeds)
                if embeds:
                    embeds[0] = deleted_embed
                else:
                    embeds = [deleted_embed]
                thread = webhook_thread_for_stored_channel(db, row["relayed_channel_id"])
                kwargs = {"embeds": embeds, "allowed_mentions": discord.AllowedMentions.none()}
                if thread:
                    kwargs["thread"] = thread
                await webhook.edit_message(int(row["relayed_message_id"]), **kwargs)
            except discord.NotFound:
                pass
            except Exception as exc:
                log.warn("REPLY-DEL", f"Failed to update reply embed {row['relayed_message_id']}: {exc}")

    def delete_relay_record(self, db: DatabaseManager, original_message_id: str, relayed_message_id: str) -> None:
        db.execute(
            """DELETE FROM relayed_messages
               WHERE original_message_id = ? AND relayed_message_id = ?""",
            (original_message_id, relayed_message_id),
        )
        db.commit()
