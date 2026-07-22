"""Shared channel utility — fetch a configurable channel by ID."""
import discord


async def fetch_configurable_channel(client: discord.Client, channel_id: str):
    """Fetch a TextChannel or ForumChannel by ID. Raises RuntimeError if not found or wrong type."""
    ch = client.get_channel(int(channel_id))
    if ch is None:
        ch = await client.fetch_channel(int(channel_id))
    if not isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
        raise RuntimeError(f"Channel {channel_id} must be Text or Forum channel.")
    return ch
