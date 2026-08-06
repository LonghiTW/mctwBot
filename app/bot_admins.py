"""Bot admin feature helpers for the config.json ``bot_admins`` schema.

Two independent permission axes exist:

- ``bot_admins[]`` — bot-level admins declared in config.json. Each entry has
  an ``id``, an optional ``name`` (human-only label, never used in logic), and
  a ``features`` map controlling which bot features that admin may use.
- Discord guild permissions — determined by Discord itself
  (``manage_guild`` / ``administrator``). Those are handled directly by the
  guild_admin cogs and are intentionally NOT part of this module.

Legacy flat lists are still honoured as fallbacks:

- ``admin.user_ids``            -> ``exclusive_command`` feature
- ``notifications.admin_user_ids`` -> ``notifications`` feature
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import CONFIG_PATH

KNOWN_FEATURES = frozenset({
    "exclusive_command",
    "notifications",
    "relay_reverse_delete",
    "announce",
})

# Legacy fallback mapping: feature -> (config section, key).
_LEGACY_FEATURE_MAP = {
    "exclusive_command": ("admin", "user_ids"),
    "notifications": ("notifications", "admin_user_ids"),
}


def _resolve_path() -> Path:
    p = Path(CONFIG_PATH)
    return p if p.is_absolute() else Path.cwd() / p


def load_config() -> dict:
    """Read config.json defensively; returns {} when missing or unreadable."""
    path = _resolve_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError):
        return {}


def _bot_admin_entries(config: dict | None = None) -> list[dict]:
    config = config if config is not None else load_config()
    entries = config.get("bot_admins", [])
    if not isinstance(entries, list):
        return []
    return [item for item in entries if isinstance(item, dict)]


def _legacy_ids(config: dict, feature: str) -> set[int]:
    if feature not in _LEGACY_FEATURE_MAP:
        return set()
    section, key = _LEGACY_FEATURE_MAP[feature]
    values = config.get(section, {}).get(key, [])
    ids: set[int] = set()
    for value in values:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def is_bot_admin(user_id: int | str) -> bool:
    """True if the user is declared in ``bot_admins[]`` or a legacy list."""
    uid = str(user_id)
    config = load_config()
    for item in _bot_admin_entries(config):
        if str(item.get("id", "")) == uid:
            return True
    for feature in _LEGACY_FEATURE_MAP:
        if uid in {str(i) for i in _legacy_ids(config, feature)}:
            return True
    return False


def bot_admin_has_feature(user_id: int | str, feature: str) -> bool:
    """Check a single bot admin feature for a user (new schema or legacy)."""
    uid = str(user_id)
    config = load_config()
    for item in _bot_admin_entries(config):
        if str(item.get("id", "")) == uid:
            features = item.get("features", {})
            return bool(features.get(feature, False))
    return uid in {str(i) for i in _legacy_ids(config, feature)}


def bot_admin_ids_with_feature(feature: str) -> set[int]:
    """All user ids granted the given feature (new schema + legacy fallback)."""
    ids = _legacy_ids(load_config(), feature)
    for item in _bot_admin_entries():
        features = item.get("features", {})
        if bool(features.get(feature, False)):
            try:
                ids.add(int(item["id"]))
            except (TypeError, ValueError, KeyError):
                continue
    return ids
