"""Time utilities for Discord snowflake conversion."""
from datetime import datetime, timedelta, timezone

DISCORD_EPOCH = 1420070400000


def discord_snowflake_to_timestamp(snowflake: int) -> datetime:
    ts_ms = (snowflake >> 22) + DISCORD_EPOCH
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def snowflake_before(days: int) -> str:
    target = datetime.now(timezone.utc) - timedelta(days=days)
    ts_ms = int(target.timestamp() * 1000)
    return str(((ts_ms - DISCORD_EPOCH) << 22))
