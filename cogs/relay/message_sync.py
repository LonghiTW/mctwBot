"""Delete synchronization for relayed Discord messages."""
import asyncio
import weakref

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
        self._ban_checks: set[tuple[str, str]] = set()
        self._recent_bans: dict[tuple[str, str], float] = {}
        self._forward_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self.webhooks = WebhookMessageClient(bot)
        relay_queue.suppress_delete_event = self.suppress_delete_event

    def suppress_delete_event(self, message_id: str) -> None:
        self._recently_deleted.add(message_id)
        asyncio.get_running_loop().call_later(60, self._recently_deleted.discard, message_id)

    async def check_banned_user(self, guild_id: str, user_id: str) -> None:
        key = (guild_id, user_id)
        loop = asyncio.get_running_loop()
        now = loop.time()
        self._recent_bans = {k: expiry for k, expiry in self._recent_bans.items() if expiry > now}
        if key in self._ban_checks or key in self._recent_bans:
            return
        self._ban_checks.add(key)
        self._recent_bans[key] = now + 30
        checked_deleted = set()
        try:
            for delay in (1, 3, 6):
                await asyncio.sleep(delay)
                db = DatabaseManager()
                rows = db.fetchall(
                    """SELECT DISTINCT original_message_id, original_channel_id
                       FROM relayed_messages WHERE original_guild_id = ? AND original_author_id = ?""",
                    key,
                )
                candidates = {str(row["original_message_id"]): str(row["original_channel_id"]) for row in rows}
                for meta in relay_queue.pending_for_author(guild_id, user_id):
                    candidates[meta["original_msg_id"]] = meta["original_channel_id"]
                for message_id, channel_id in candidates.items():
                    if message_id in checked_deleted:
                        continue
                    try:
                        channel = await self.bot.fetch_channel(int(channel_id))
                    except Exception as exc:
                        log.warn("BAN-CHECK", f"Cannot fetch channel {channel_id}: {exc}")
                        continue
                    try:
                        await channel.fetch_message(int(message_id))
                    except discord.NotFound as exc:
                        if exc.code != 10008:
                            continue
                        try:
                            source_channel_id = str(getattr(channel, "parent_id", None) or channel_id)
                            complete = await self.sync_forward_delete(message_id, source_channel_id, ban_cleanup=True)
                            if complete:
                                checked_deleted.add(message_id)
                        except Exception as cleanup_exc:
                            log.warn("BAN-CHECK", f"Cleanup failed for {message_id}: {cleanup_exc}")
                    except Exception as exc:
                        log.warn("BAN-CHECK", f"Cannot fetch message {message_id}: {exc}")
        except Exception as exc:
            log.warn("BAN-CHECK", f"Ban cleanup failed for guild {guild_id}, user {user_id}: {exc}")
        finally:
            self._ban_checks.discard(key)

    async def sync_reverse_delete(self, relayed_message_id: str, guild: discord.Guild | None = None) -> bool:
        if relayed_message_id in self._recently_deleted:
            return True
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
        if not bool(src and src["allow_reverse_delete"]):
            log.info("DEL-REV", f"Reverse delete denied for relayed copy {relayed_message_id}")
            return True
        try:
            channel = await self.bot.fetch_channel(int(link["original_channel_id"]))
            original = await channel.fetch_message(int(link["original_message_id"]))
            await original.delete()
            log.info("DEL-REV", f"Reverse deleted original {link['original_message_id']} (triggered by {relayed_message_id})")
        except Exception as exc:
            log.warn("DEL-REV", f"Reverse delete failed for {relayed_message_id}: {exc}")
        return True

    async def delete_relay_chain(self, message_id: str) -> dict:
        """Delete an original message and all relayed copies by any mapped ID."""
        db = DatabaseManager()
        store = RelayMessageStore(db)
        link = store.original_for_relayed(message_id)
        if link:
            original_id = str(link["original_message_id"])
            original_channel_id = str(link["original_channel_id"])
        else:
            original_id = str(message_id)
            original = store.original_channel_for_original(original_id)
            if not original:
                return {"found": False, "deleted": 0, "failed": []}
            original_channel_id = str(original["original_channel_id"])

        relayed = store.relayed_for_original(original_id)
        message_ids = {original_id, *(str(row["relayed_message_id"]) for row in relayed)}
        self._recently_deleted.update(message_ids)
        loop = asyncio.get_running_loop()
        for suppressed_id in message_ids:
            loop.call_later(5, self._recently_deleted.discard, suppressed_id)

        deleted = 0
        failed: list[str] = []
        try:
            channel = await self.bot.fetch_channel(int(original_channel_id))
            original_message = await channel.fetch_message(int(original_id))
            await original_message.delete()
            deleted += 1
        except discord.NotFound:
            deleted += 1
        except Exception as exc:
            failed.append(f"原始訊息 {original_id}: {exc}")

        for row in relayed:
            relayed_id = str(row["relayed_message_id"])
            try:
                await self.webhooks.delete_message(db, row["relayed_channel_id"], relayed_id)
                deleted += 1
            except discord.NotFound:
                deleted += 1
            except Exception as exc:
                failed.append(f"同步訊息 {relayed_id}: {exc}")

        if not failed:
            store.delete_mappings_for_original(original_id)
            await self.mark_replied_message_deleted(db, original_id)

        return {"found": True, "deleted": deleted, "failed": failed}

    async def sync_forward_delete(self, original_message_id: str, channel_id: str, *, ban_cleanup: bool = False) -> bool:
        lock = self._forward_locks.setdefault(original_message_id, asyncio.Lock())
        async with lock:
            if not ban_cleanup:
                relay_queue.cancel(original_message_id)
                if original_message_id in self._recently_deleted:
                    return True
            self._recently_deleted.add(original_message_id)
            asyncio.get_running_loop().call_later(5, self._recently_deleted.discard, original_message_id)
            return await self._delete_forward_copies(original_message_id, channel_id, ban_cleanup=ban_cleanup)

    async def _delete_forward_copies(self, original_message_id: str, channel_id: str, *, ban_cleanup: bool) -> bool:
        db = DatabaseManager()
        store = RelayMessageStore(db)
        await self.mark_replied_message_deleted(db, original_message_id)

        src = db.fetchone(
            "SELECT allow_forward_delete FROM linked_channels WHERE channel_id = ?",
            (configured_channel_id_for_stored_channel(db, channel_id),),
        )
        if not src or not src["allow_forward_delete"]:
            return ban_cleanup

        if ban_cleanup:
            relay_queue.cancel(original_message_id)
        relayed = store.relayed_for_original(original_message_id)
        if not relayed:
            return ban_cleanup

        deleted = 0
        failed = 0
        for row in relayed:
            relayed_message_id = str(row["relayed_message_id"])
            self._recently_deleted.add(relayed_message_id)
            asyncio.get_running_loop().call_later(5, self._recently_deleted.discard, relayed_message_id)
            try:
                await self.webhooks.delete_message(db, row["relayed_channel_id"], relayed_message_id)
                deleted += 1
                store.delete_mapping(original_message_id, relayed_message_id)
            except LookupError as exc:
                failed += 1
                log.warn("DEL-FWD", str(exc))
            except discord.NotFound as exc:
                if exc.code == 10008:
                    deleted += 1
                    store.delete_mapping(original_message_id, relayed_message_id)
                else:
                    failed += 1
                    log.warn("DEL-FWD", f"Cannot locate relayed message {relayed_message_id}: {exc}")
            except Exception as exc:
                failed += 1
                log.warn("DEL-FWD", f"Delete failed {relayed_message_id} in {row['relayed_channel_id']}: {exc}")

        log.info("DEL-FWD", f"Deleted {deleted}/{len(relayed)} relayed copies for {original_message_id}; failed={failed}")
        return not failed if ban_cleanup else True

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

