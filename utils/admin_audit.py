"""Audit logging for admin actions, with DM notification to bot admins.

Every usage of an audited admin command is written to the backend log
(``ADMIN-AUDIT`` tag) and a DM is sent to every bot admin that has the
``notifications`` feature enabled. Long details are truncated so DMs stay
short.
"""
from utils.log_manager import LogManager
from utils.admin_notifier import notify_admins

log = LogManager


async def audit_admin_usage(bot, ctx, action: str, detail: str, truncate: int = 500) -> None:
    author = getattr(ctx, "author", None) or getattr(ctx, "user", None)
    if author is None:
        return
    text = detail if len(detail) <= truncate else detail[:truncate] + "..."
    log.info("ADMIN-AUDIT", f"{action} | 使用者：{author} ({author.id}) | {text.replace(chr(10), ' / ')}")
    await notify_admins(bot, f"管理操作：{action}", f"使用者：{author} ({author.id})\n{text}")
