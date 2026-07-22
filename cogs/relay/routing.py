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
    """Return webhook metadata for routing to forum channels.

    Forum channels require a thread_name or thread_id for webhook posts.
    One relay thread is maintained per source channel in each target forum.
    Regular text channels need no thread routing.
    """
    target_parent = await fetch_configurable_channel(client, target_parent_channel_id)

    if not isinstance(target_parent, discord.ForumChannel):
        return {}

    source_key = linked_channel_id_for_message(source_message)

    existing = db.fetchone(
        """SELECT target_thread_id FROM relay_threads
           WHERE group_id = ? AND source_thread_id = ? AND target_parent_channel_id = ?""",
        (group_id, source_key, target_parent_channel_id),
    )
    if existing:
        return {"target_thread_id": existing["target_thread_id"]}

    # First time — webhook will create a new forum post, queue.py saves the mapping
    return {
        "thread_name": f"Relay from {source_message.guild.name}",
        "source_thread_id": source_key,
        "source_parent_channel_id": source_key,
        "target_parent_channel_id": target_parent_channel_id,
        "group_id": group_id,
    }
