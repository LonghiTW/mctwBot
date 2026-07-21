"""Automatic webhook repair when a webhook becomes invalid."""
from discord import Client, TextChannel, ForumChannel

from database import DatabaseManager
from utils.log_manager import LogManager

log = LogManager


class WebhookManager:
    """Recreates a deleted webhook and updates the database."""

    async def handle_invalid_webhook(
        self,
        client: Client,
        channel_id: str,
        group_name: str = "Unknown",
    ):
        if not client:
            return None

        log.warn("WEBHOOK", f"Repairing webhook for {channel_id}...")

        try:
            channel = client.get_channel(int(channel_id))
            if channel is None:
                channel = await client.fetch_channel(int(channel_id))
            if channel is None:
                raise RuntimeError("Channel inaccessible")
            if not isinstance(channel, (TextChannel, ForumChannel)):
                raise RuntimeError("Not a text/forum channel")

            new_wh = await channel.create_webhook(
                name="MCTW Relay",
                reason=f"Auto-repair: {group_name}",
            )
            db = DatabaseManager()
            db.execute(
                "UPDATE linked_channels SET webhook_url = ? WHERE channel_id = ?",
                (new_wh.url, channel_id),
            )
            db.commit()
            log.info("WEBHOOK", f"Repaired for {channel_id}")
            return new_wh

        except Exception as exc:
            log.error("WEBHOOK", f"Repair failed for {channel_id}: {exc}")
            try:
                ch = client.get_channel(int(channel_id))
                if ch and isinstance(ch, (TextChannel, ForumChannel)):
                    await ch.send(
                        "⚠️ **中繼連線中斷：** Webhook 已失效且無法自動修復。\n"
                        "請確保機器人有「管理 Webhook」權限。"
                    )
            except Exception:
                pass
            try:
                DatabaseManager().execute(
                    "DELETE FROM linked_channels WHERE channel_id = ?", (channel_id,)
                ).commit()
            except Exception:
                pass
            return None
