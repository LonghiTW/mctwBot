"""Helpers for routing normal channels, threads, and forum posts."""
import discord

from database import DatabaseManager
from utils.channel_utils import fetch_configurable_channel
from utils.log_manager import LogManager


log = LogManager


def is_thread_channel(channel) -> bool:
    return isinstance(channel, discord.Thread)


def linked_channel_id_for_message(message: discord.Message) -> str:
    if is_thread_channel(message.channel) and message.channel.parent_id:
        return str(message.channel.parent_id)
    return str(message.channel.id)


def configured_channel_id_for_stored_channel(db: DatabaseManager, channel_id: str) -> str:
    linked = db.fetchone(
        "SELECT channel_id FROM linked_channels WHERE channel_id = ?", (channel_id,)
    )
    if linked:
        return linked["channel_id"]

    row = db.fetchone(
        """SELECT target_parent_channel_id FROM relay_threads
           WHERE target_thread_id = ? LIMIT 1""",
        (channel_id,),
    )
    if row:
        return row["target_parent_channel_id"]

    row = db.fetchone(
        """SELECT source_parent_channel_id FROM relay_threads
           WHERE source_thread_id = ? LIMIT 1""",
        (channel_id,),
    )
    if row:
        return row["source_parent_channel_id"]

    return channel_id


def webhook_thread_for_stored_channel(db: DatabaseManager, channel_id: str) -> discord.Object | None:
    linked = db.fetchone(
        "SELECT channel_id FROM linked_channels WHERE channel_id = ?", (channel_id,)
    )
    if linked:
        return None

    row = db.fetchone(
        """SELECT 1 FROM relay_threads
           WHERE target_thread_id = ? OR source_thread_id = ? LIMIT 1""",
        (channel_id, channel_id),
    )
    if not row:
        return None

    return discord.Object(id=int(channel_id))


async def prepare_thread_route(
    client: discord.Client,
    db: DatabaseManager,
    group_id: int,
    source_message: discord.Message,
    target_parent_channel_id: str,
) -> dict:
    """Return webhook metadata for routing threads/forum posts.

    Cases:
    1. Regular message → any: no routing
    2. Thread/forum post → TextChannel: mirror thread
    3. Thread/forum post → ForumChannel: mirror post via thread_name
    4. Mirrored thread → resolve to original source, then route accordingly
    """
    if not is_thread_channel(source_message.channel):
        return {}

    target_parent = await fetch_configurable_channel(client, target_parent_channel_id)
    current_thread_id = str(source_message.channel.id)
    source_thread_id = str(source_message.channel.id)
    source_parent_id = str(source_message.channel.parent_id)
    route_thread_name = source_message.channel.name or "Relayed thread"
    route_guild_name = source_message.guild.name

    # Resolve: if this thread is itself a mirrored thread, use original source IDs
    mirrored = db.fetchone(
        """SELECT source_thread_id, source_parent_channel_id
           FROM relay_threads
           WHERE group_id = ? AND target_thread_id = ? LIMIT 1""",
        (group_id, source_thread_id),
    )
    if mirrored:
        source_thread_id = mirrored["source_thread_id"]
        source_parent_id = mirrored["source_parent_channel_id"]
        try:
            original_thread = await client.fetch_channel(int(source_thread_id))
            route_thread_name = original_thread.name or route_thread_name
            route_guild_name = original_thread.guild.name
        except Exception:
            pass
        log.info("THREAD-ROUTE", f"Resolved mirror {current_thread_id} -> {source_thread_id}")
    else:
        recovered = db.fetchone(
            """SELECT original_channel_id
               FROM relayed_messages
               WHERE relayed_message_id = ?
               ORDER BY id LIMIT 1""",
            (source_thread_id,),
        )
        if not recovered:
            recovered = db.fetchone(
                """SELECT original_channel_id
                   FROM relayed_messages
                   WHERE relayed_channel_id = ?
                   ORDER BY id LIMIT 1""",
                (source_thread_id,),
            )
        if recovered and recovered["original_channel_id"] != source_thread_id:
            source_thread_id = recovered["original_channel_id"]
            mirrored_origin = db.fetchone(
                """SELECT source_thread_id, source_parent_channel_id
                   FROM relay_threads
                   WHERE group_id = ? AND target_thread_id = ? LIMIT 1""",
                (group_id, source_thread_id),
            )
            if mirrored_origin:
                source_thread_id = mirrored_origin["source_thread_id"]
                source_parent_id = mirrored_origin["source_parent_channel_id"]
            else:
                try:
                    original_thread = await client.fetch_channel(int(source_thread_id))
                    source_parent_id = str(original_thread.parent_id)
                    route_thread_name = original_thread.name or route_thread_name
                    route_guild_name = original_thread.guild.name
                except Exception:
                    pass
            db.execute(
                """INSERT OR IGNORE INTO relay_threads
                   (group_id, source_thread_id, source_parent_channel_id,
                    target_parent_channel_id, target_thread_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (group_id, source_thread_id, source_parent_id,
                 str(source_message.channel.parent_id), current_thread_id),
            )
            db.commit()
            log.info("THREAD-ROUTE", f"Recovered mirror {current_thread_id} -> {source_thread_id}")

    # Check existing mapping (source → target for this target channel)
    existing = db.fetchone(
        """SELECT target_thread_id FROM relay_threads
           WHERE group_id = ? AND source_thread_id = ? AND target_parent_channel_id = ?""",
        (group_id, source_thread_id, target_parent_channel_id),
    )
    if existing:
        log.info("THREAD-ROUTE", f"Using mapped thread {source_thread_id} -> {existing['target_thread_id']}")
        return {"target_thread_id": existing["target_thread_id"]}

    # If target is the source thread's own parent channel, send back to original
    if source_parent_id == target_parent_channel_id:
        log.info("THREAD-ROUTE", f"Routing back to source thread {source_thread_id}")
        return {"target_thread_id": source_thread_id}

    # Build thread name
    if isinstance(target_parent, discord.ForumChannel):
        orig = route_thread_name[:92] or "Relay"
        thread_name = f"{orig}({route_guild_name})"[:100]
    else:
        thread_name = route_thread_name[:100]

    if isinstance(target_parent, discord.TextChannel):
        # Create mirror thread in target TextChannel
        created = await target_parent.create_thread(
            name=thread_name,
            type=discord.ChannelType.public_thread,
            reason="Relay thread mirror",
        )
        db.execute(
            """INSERT OR REPLACE INTO relay_threads
               (group_id, source_thread_id, source_parent_channel_id,
                target_parent_channel_id, target_thread_id)
               VALUES (?, ?, ?, ?, ?)""",
            (group_id, source_thread_id, source_parent_id,
             target_parent_channel_id, str(created.id)),
        )
        db.commit()
        return {"target_thread_id": str(created.id)}

    # ForumChannel target — webhook will create the post, queue.py saves mapping
    return {
        "thread_name": thread_name,
        "source_thread_id": source_thread_id,
        "source_parent_channel_id": source_parent_id,
        "target_parent_channel_id": target_parent_channel_id,
        "group_id": group_id,
    }
