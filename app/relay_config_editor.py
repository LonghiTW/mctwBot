"""Pure helpers for editing relay groups/channels in config dictionaries."""
from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path

from app.config import CONFIG_PATH
from app.config_validator import validate_config

VALID_DIRECTIONS = {"BOTH", "SEND_ONLY", "RECEIVE_ONLY"}


class RelayConfigEditError(ValueError):
    pass


def resolve_config_path() -> Path:
    path = Path(CONFIG_PATH)
    return path if path.is_absolute() else Path.cwd() / path


def load_config_file(path: Path | None = None) -> dict:
    config_path = path or resolve_config_path()
    with config_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise RelayConfigEditError("config.json must contain an object.")
    return data


def save_config_file(config: dict, path: Path | None = None) -> None:
    validate_config(config)
    config_path = path or resolve_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(tmp_path, config_path)


def backup_config(path: Path | None = None) -> Path:
    config_path = path or resolve_config_path()
    backup_dir = config_path.parent / "data" / "config-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{config_path.name}.bak"
    index = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{config_path.name}.{index}.bak"
        index += 1
    shutil.copy2(config_path, backup_path)
    return backup_path


def group_names(config: dict) -> list[str]:
    return [str(group.get("name", "")) for group in _groups(config) if str(group.get("name", "")).strip()]


def channel_ids(config: dict) -> set[str]:
    ids: set[str] = set()
    for group in _groups(config):
        for channel in group.get("channels", []) or []:
            channel_id = str(channel.get("channel_id", "")).strip()
            if channel_id:
                ids.add(channel_id)
    return ids


def add_group(config: dict, name: str, hidden: bool = False) -> dict:
    result = deepcopy(config)
    groups = _groups(result)
    clean_name = _clean_group_name(name)
    if _find_group(groups, clean_name):
        raise RelayConfigEditError(f"Relay group already exists: {clean_name}")
    groups.append({"name": clean_name, "hidden": bool(hidden), "channels": []})
    _set_groups(result, groups)
    return result


def edit_group(config: dict, group_name: str, new_name: str | None = None, hidden: bool | None = None) -> dict:
    if new_name is None and hidden is None:
        raise RelayConfigEditError("No group fields were provided.")
    result = deepcopy(config)
    groups = _groups(result)
    group = _require_group(groups, group_name)
    old_name = str(group["name"])
    if new_name is not None:
        clean_name = _clean_group_name(new_name)
        existing = _find_group(groups, clean_name)
        if existing is not None and existing is not group:
            raise RelayConfigEditError(f"Relay group already exists: {clean_name}")
        group["name"] = clean_name
        _rename_role_mapping_groups(result, old_name, clean_name)
    if hidden is not None:
        group["hidden"] = bool(hidden)
    return result


def remove_group(config: dict, group_name: str) -> tuple[dict, dict]:
    result = deepcopy(config)
    groups = _groups(result)
    group = _require_group(groups, group_name)
    removed = deepcopy(group)
    groups.remove(group)
    _set_groups(result, groups)
    relay = _relay(result)
    relay["role_mappings"] = [
        mapping for mapping in relay.get("role_mappings", []) or []
        if str(mapping.get("group_name", "")).strip() != str(removed.get("name", "")).strip()
    ]
    return result, removed


def add_channel(
    config: dict,
    group_name: str,
    channel_id: int | str,
    channel_kind: str,
    direction: str = "BOTH",
    brand_name: str | None = None,
    process_bot_messages: bool = False,
    allow_forward_delete: bool = True,
    allow_reverse_delete: bool = False,
) -> dict:
    result = deepcopy(config)
    groups = _groups(result)
    group = _require_group(groups, group_name)
    clean_channel_id = _clean_channel_id(channel_id)
    if _find_channel(groups, clean_channel_id):
        raise RelayConfigEditError(f"Relay channel already exists: {clean_channel_id}")
    _assert_group_channel_kind(group, channel_kind)
    channel = {
        "channel_id": clean_channel_id,
        "direction": _clean_direction(direction),
        "process_bot_messages": bool(process_bot_messages),
        "allow_forward_delete": bool(allow_forward_delete),
        "allow_reverse_delete": bool(allow_reverse_delete),
    }
    if brand_name is not None:
        channel["brand_name"] = str(brand_name).strip()
    group.setdefault("channels", []).append(channel)
    return result


def edit_channel(
    config: dict,
    channel_id: int | str,
    channel_kind: str,
    group_name: str | None = None,
    direction: str | None = None,
    brand_name: str | None = None,
    clear_brand_name: bool = False,
    process_bot_messages: bool | None = None,
    allow_forward_delete: bool | None = None,
    allow_reverse_delete: bool | None = None,
) -> tuple[dict, str, dict]:
    if clear_brand_name and brand_name is not None:
        raise RelayConfigEditError("brand_name and clear_brand_name cannot be used together.")
    if all(value is None for value in (group_name, direction, brand_name, process_bot_messages, allow_forward_delete, allow_reverse_delete)) and not clear_brand_name:
        raise RelayConfigEditError("No channel fields were provided.")

    result = deepcopy(config)
    groups = _groups(result)
    clean_channel_id = _clean_channel_id(channel_id)
    current_group, channel = _require_channel(groups, clean_channel_id)
    old_group_name = str(current_group.get("name", ""))

    if group_name is not None:
        target_group = _require_group(groups, group_name)
        if target_group is not current_group:
            _assert_group_channel_kind(target_group, channel_kind)
            current_group["channels"].remove(channel)
            target_group.setdefault("channels", []).append(channel)
            current_group = target_group

    if direction is not None:
        channel["direction"] = _clean_direction(direction)
    if clear_brand_name:
        channel["brand_name"] = ""
    elif brand_name is not None:
        channel["brand_name"] = str(brand_name).strip()
    if process_bot_messages is not None:
        channel["process_bot_messages"] = bool(process_bot_messages)
    if allow_forward_delete is not None:
        channel["allow_forward_delete"] = bool(allow_forward_delete)
    if allow_reverse_delete is not None:
        channel["allow_reverse_delete"] = bool(allow_reverse_delete)

    return result, old_group_name, deepcopy(channel)


def remove_channel(config: dict, channel_id: int | str) -> tuple[dict, str, dict]:
    result = deepcopy(config)
    groups = _groups(result)
    clean_channel_id = _clean_channel_id(channel_id)
    group, channel = _require_channel(groups, clean_channel_id)
    group["channels"].remove(channel)
    return result, str(group.get("name", "")), deepcopy(channel)


def _relay(config: dict) -> dict:
    relay = config.setdefault("relay", {})
    if not isinstance(relay, dict):
        raise RelayConfigEditError("relay must be an object.")
    return relay


def _groups(config: dict) -> list[dict]:
    relay = _relay(config)
    groups = relay.setdefault("groups", [])
    if not isinstance(groups, list):
        raise RelayConfigEditError("relay.groups must be an array.")
    return groups


def _set_groups(config: dict, groups: list[dict]) -> None:
    _relay(config)["groups"] = groups


def _clean_group_name(name: str) -> str:
    clean_name = str(name).strip()
    if not clean_name:
        raise RelayConfigEditError("Relay group name is required.")
    return clean_name


def _clean_channel_id(channel_id: int | str) -> str:
    clean_channel_id = str(channel_id).strip()
    if not clean_channel_id.isdigit():
        raise RelayConfigEditError("Channel id must be numeric.")
    return clean_channel_id


def _clean_direction(direction: str) -> str:
    clean_direction = str(direction).upper().strip()
    if clean_direction not in VALID_DIRECTIONS:
        raise RelayConfigEditError(f"Direction must be one of: {', '.join(sorted(VALID_DIRECTIONS))}.")
    return clean_direction


def _find_group(groups: list[dict], name: str) -> dict | None:
    clean_name = _clean_group_name(name)
    for group in groups:
        if str(group.get("name", "")).strip() == clean_name:
            return group
    return None


def _require_group(groups: list[dict], name: str) -> dict:
    group = _find_group(groups, name)
    if group is None:
        raise RelayConfigEditError(f"Relay group not found: {name}")
    return group


def _find_channel(groups: list[dict], channel_id: str) -> tuple[dict, dict] | None:
    for group in groups:
        for channel in group.get("channels", []) or []:
            if str(channel.get("channel_id", "")).strip() == channel_id:
                return group, channel
    return None


def _require_channel(groups: list[dict], channel_id: str) -> tuple[dict, dict]:
    found = _find_channel(groups, channel_id)
    if found is None:
        raise RelayConfigEditError(f"Relay channel not found: {channel_id}")
    return found


def _assert_group_channel_kind(group: dict, channel_kind: str) -> None:
    channels = group.get("channels", []) or []
    existing_kind = group.get("channel_kind")
    if not existing_kind and channels:
        existing_kind = str(channels[0].get("channel_kind", "")).strip() or None
    if existing_kind and str(existing_kind) != str(channel_kind):
        raise RelayConfigEditError("Relay group channels must use the same channel type.")
    if channel_kind:
        group["channel_kind"] = str(channel_kind)


def _rename_role_mapping_groups(config: dict, old_name: str, new_name: str) -> None:
    relay = _relay(config)
    for mapping in relay.get("role_mappings", []) or []:
        if str(mapping.get("group_name", "")).strip() == old_name:
            mapping["group_name"] = new_name
