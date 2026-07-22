"""Helpers for routing normal channels, threads, and forum posts."""
import discord

from database import DatabaseManager
from utils.channel_utils import fetch_configurable_channel


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
    """
    if not is_thread_channel(source_message.channel):
        return {}

    target_parent = await fetch_configurable_channel(client, target_parent_channel_id)
    source_thread_id = str(source_message.channel.id)
    source_parent_id = str(source_message.channel.parent_id)

    # Check existing mapping
    existing = db.fetchone(
        """SELECT target_thread_id FROM relay_threads
           WHERE group_id = ? AND source_thread_id = ? AND target_parent_channel_id = ?""",
        (group_id, source_thread_id, target_parent_channel_id),
    )
    if existing:
        return {"target_thread_id": existing["target_thread_id"]}

    # Build thread name
    if isinstance(target_parent, discord.ForumChannel):
        # Forum → Forum: "original title (server name)"
        orig = source_message.channel.name[:92] or "Relay"
        thread_name = f"{orig}({source_message.guild.name})"[:100]
    else:
        # Thread → TextChannel: use original thread name
        thread_name = (source_message.channel.name or "Relayed thread")[:100]

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
