"""DM notifications to configured administrators."""
import json
from pathlib import Path

from config import CONFIG_PATH
from .log_manager import LogManager

log = LogManager


def _resolve_path() -> Path:
    p = Path(CONFIG_PATH)
    return p if p.is_absolute() else Path.cwd() / p


def load_admin_user_ids() -> list[str]:
    path = _resolve_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            config = json.load(f)
            return config.get("notifications", {}).get("admin_user_ids", [])
    except (json.JSONDecodeError, IOError):
        return []


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
