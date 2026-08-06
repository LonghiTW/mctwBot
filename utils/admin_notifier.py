"""DM notifications to configured bot admins."""
from app.bot_admins import bot_admin_ids_with_feature
from .log_manager import LogManager

log = LogManager


def load_admin_user_ids() -> list[str]:
    """User ids granted the ``notifications`` bot admin feature."""
    return [str(uid) for uid in bot_admin_ids_with_feature("notifications")]


async def notify_admins(client, title: str, message: str):
    """Send a DM to every configured admin user."""
    ids = load_admin_user_ids()
    if not ids:
        return
    for uid in ids:
        try:
            user = await client.fetch_user(int(uid))
            await user.send(f"**{title}**\n\n{message}")
        except Exception as exc:
            log.warn("NOTIFY", f"Failed to DM {uid}: {exc}")
