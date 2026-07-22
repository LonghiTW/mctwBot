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


def parent_channel_id_for_message(message: discord.Message) -> str:
    if is_thread_channel(message.channel) and message.channel.parent_id:
        return str(message.channel.parent_id)
    return str(message.channel.id)


def configured_channel_id_for_stored_channel(db: DatabaseManager, channel_id: str) -> str:
    linked = db.fetchone(
        "SELECT channel_id FROM linked_channels WHERE channel_id = ?", (channel_id,)
    )
    if linked:
        return linked["channel_id"]

    for tbl, col in [
        ("relay_threads", "target_thread_id"),
        ("relay_threads", "source_thread_id"),
    ]:
        row = db.fetchone(
            f"SELECT target_parent_channel_id FROM {tbl} WHERE {col} = ? LIMIT 1",
            (channel_id,),
        )
        if row:
            return row["target_parent_channel_id"]

    return channel_id


async def prepare_thread_route(
    client: discord.Client,
    db: DatabaseManager,
    group_id: int,
    source_message: discord.Message,
    target_parent_channel_id: str,
) -> dict:
    """Return webhook metadata for routing to the correct thread/forum post.

    Handles four cases:
    1. Regular channel → regular channel: no routing
    2. Thread/forum post → regular channel: mirror thread (existing)
    3. Thread/forum post → forum channel: mirror via webhook thread_name
    4. Regular channel → forum channel: create relay thread in forum
    """
    target_parent = await fetch_configurable_channel(client, target_parent_channel_id)
    is_forum_target = isinstance(target_parent, discord.ForumChannel)

    source_is_thread = is_thread_channel(source_message.channel)

    if not source_is_thread and not is_forum_target:
        return {}

    # Determine mapping key
    if source_is_thread:
        source_thread_id = str(source_message.channel.id)
        source_parent_channel_id = str(source_message.channel.parent_id)
    else:
        # Non-thread → forum: use channel ID as mapping key
        source_thread_id = str(source_message.channel.id)
        source_parent_channel_id = str(source_message.channel.id)

    # Check existing mapping
    existing = db.fetchone(
        """SELECT target_thread_id FROM relay_threads
           WHERE group_id = ? AND source_thread_id = ? AND target_parent_channel_id = ?""",
        (group_id, source_thread_id, target_parent_channel_id),
    )
    if existing:
        return {
            "target_thread_id": existing["target_thread_id"],
            "source_thread_id": source_thread_id,
            "source_parent_channel_id": source_parent_channel_id,
            "target_parent_channel_id": target_parent_channel_id,
        }

    thread_name = (
        source_message.channel.name[:100]
        if source_is_thread
        else f"Relay from {source_message.guild.name}"
    )

    if isinstance(target_parent, discord.TextChannel):
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
            (group_id, source_thread_id, source_parent_channel_id,
             target_parent_channel_id, str(created.id)),
        )
        db.commit()
        return {
            "target_thread_id": str(created.id),
            "source_thread_id": source_thread_id,
            "source_parent_channel_id": source_parent_channel_id,
            "target_parent_channel_id": target_parent_channel_id,
        }

    # ForumChannel target — webhook creates the thread/post on first send
    return {
        "thread_name": thread_name,
        "source_thread_id": source_thread_id,
        "source_parent_channel_id": source_parent_channel_id,
        "target_parent_channel_id": target_parent_channel_id,
        "group_id": group_id,
    }
