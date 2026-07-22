"""Load config.json and sync relay groups/channels into SQLite."""
import json
from pathlib import Path

import discord

from config import CONFIG_PATH
from database import DatabaseManager
from utils.log_manager import LogManager
from utils.admin_notifier import notify_admins
from utils.channel_utils import fetch_configurable_channel

log = LogManager

VALID_DIRECTIONS = {"BOTH", "SEND_ONLY", "RECEIVE_ONLY"}


def _resolve_path() -> Path:
    p = Path(CONFIG_PATH)
    return p if p.is_absolute() else Path.cwd() / p


def load_config() -> dict:
    """Read and return the full config.json."""
    path = _resolve_path()
    if not path.exists():
        raise RuntimeError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


async def sync_configured_relays(client: discord.Client) -> None:
    """Sync relay groups from config.json into the database.

    Inaccessible channels are skipped gracefully; errors are reported
    to configured admin users via DM.
    """
    config = load_config()
    relay_cfg = config.get("relay", {})
    groups = relay_cfg.get("groups", [])
    if not groups:
        log.info("CONFIG", "No relay groups configured, skipping sync.")
        return

    db = DatabaseManager()
    configured_channel_ids: set[str] = set()
    configured_group_names: set[str] = set()
    sync_errors: list[str] = []

    for group_cfg in groups:
        group_name = str(group_cfg.get("name", "")).strip()
        if not group_name:
            sync_errors.append("⚠️ 跳過了一個沒有名稱的群組。")
            continue

        channels = group_cfg.get("channels") or []
        if not channels:
            sync_errors.append(f"⚠️ 群組「{group_name}」沒有頻道，已跳過。")
            continue

        try:
            first = await fetch_configurable_channel(
                client, str(channels[0]["channel_id"])
            )
            owner_guild_id = str(first.guild.id)
        except Exception as exc:
            sync_errors.append(
                f"❌ 群組「{group_name}」無法存取：{exc}，已跳過。"
            )
            continue

        configured_group_names.add(group_name)
        db.execute(
            """INSERT INTO relay_groups (group_name, owner_guild_id, owner_user_id)
               VALUES (?, ?, NULL)
               ON CONFLICT(group_name) DO UPDATE SET owner_guild_id = excluded.owner_guild_id""",
            (group_name, owner_guild_id),
        )
        db.commit()

        group = db.fetchone(
            "SELECT group_id FROM relay_groups WHERE group_name = ?", (group_name,)
        )
        group_id = group["group_id"]

        for ch_cfg in channels:
            channel_id = str(ch_cfg.get("channel_id", "")).strip()
            if not channel_id:
                sync_errors.append(
                    f"⚠️ 群組「{group_name}」有空 channel_id，已跳過。"
                )
                continue

            try:
                channel = await fetch_configurable_channel(client, channel_id)
            except Exception as exc:
                sync_errors.append(
                    f"❌ 群組「{group_name}」頻道 {channel_id} 無法存取：{exc}"
                )
                continue

            configured_channel_ids.add(channel_id)

            direction = str(ch_cfg.get("direction", "BOTH")).upper()
            if direction not in VALID_DIRECTIONS:
                sync_errors.append(
                    f"⚠️ 群組「{group_name}」頻道 {channel_id} 方向「{direction}」無效"
                )
                continue

            existing = db.fetchone(
                "SELECT webhook_url FROM linked_channels WHERE channel_id = ?",
                (channel_id,),
            )
            webhook_url = existing["webhook_url"] if existing else None
            if not webhook_url:
                try:
                    wh = await channel.create_webhook(
                        name="MCTW Relay",
                        reason=f"Relay group: {group_name}",
                    )
                    webhook_url = wh.url
                except Exception as exc:
                    sync_errors.append(
                        f"❌ 群組「{group_name}」頻道 {channel_id} 無法建立 webhook：{exc}"
                    )
                    continue

            db.execute(
                """INSERT INTO linked_channels
                   (channel_id, guild_id, group_id, webhook_url, direction, brand_name,
                    allow_forward_delete, allow_reverse_delete, process_bot_messages,
                    allow_auto_role_creation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(channel_id) DO UPDATE SET
                       guild_id = excluded.guild_id,
                       group_id = excluded.group_id,
                       webhook_url = excluded.webhook_url,
                       direction = excluded.direction,
                       brand_name = excluded.brand_name,
                       allow_forward_delete = excluded.allow_forward_delete,
                       allow_reverse_delete = excluded.allow_reverse_delete,
                       process_bot_messages = excluded.process_bot_messages""",
                (
                    channel_id,
                    str(channel.guild.id),
                    group_id,
                    webhook_url,
                    direction,
                    ch_cfg.get("brand_name") or channel.guild.name,
                    1 if ch_cfg.get("allow_forward_delete", True) else 0,
                    1 if ch_cfg.get("allow_reverse_delete", False) else 0,
                    1 if ch_cfg.get("process_bot_messages", False) else 0,
                ),
            )

        # Role mappings
        db.execute("DELETE FROM role_mappings WHERE group_id = ?", (group_id,))
        for mapping in group_cfg.get("role_mappings", []):
            common_name = str(mapping.get("common_name", "")).strip()
            guild_id = str(mapping.get("guild_id", "")).strip()
            role_id = str(mapping.get("role_id", "")).strip()
            if not common_name or not guild_id or not role_id:
                sync_errors.append(f"⚠️ 群組「{group_name}」有無效的角色映射。")
                continue
            db.execute(
                """INSERT OR REPLACE INTO role_mappings
                   (group_id, guild_id, role_name, role_id)
                   VALUES (?, ?, ?, ?)""",
                (group_id, guild_id, common_name, role_id),
            )
        db.commit()

    # Clean removed channels / groups
    if configured_channel_ids:
        ph = ",".join("?" for _ in configured_channel_ids)
        db.execute(f"DELETE FROM linked_channels WHERE channel_id NOT IN ({ph})", tuple(configured_channel_ids))
        db.commit()
    if configured_group_names:
        ph = ",".join("?" for _ in configured_group_names)
        db.execute(f"DELETE FROM relay_groups WHERE group_name NOT IN ({ph})", tuple(configured_group_names))
        db.commit()

    log.info("CONFIG", f"Synced {len(configured_group_names)} group(s), {len(configured_channel_ids)} channel(s), {len(sync_errors)} error(s).")

    if sync_errors and client:
        await notify_admins(
            client,
            "⚙️ 中繼同步結果",
            "設定同步完成，但發生以下問題：\n\n" + "\n\n".join(sync_errors) +
            "\n\n請檢查 config.json 後重新啟動機器人。",
        )
