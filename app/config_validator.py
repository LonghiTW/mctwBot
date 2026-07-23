"""Startup validation for config.json."""
from __future__ import annotations

KNOWN_FEATURES = {"relay", "keywords", "scheduler", "moderation", "commands", "admin"}
VALID_DIRECTIONS = {"BOTH", "SEND_ONLY", "RECEIVE_ONLY"}


def validate_config(config: dict) -> None:
    errors: list[str] = []

    if not isinstance(config, dict):
        raise RuntimeError("config.json must contain a JSON object.")

    if "admin_user_ids" in config:
        errors.append("admin_user_ids moved to notifications.admin_user_ids.")
    admin = _object_or_empty(config.get("admin", {}), "admin", errors)
    notifications = _object_or_empty(config.get("notifications", {}), "notifications", errors)
    _validate_id_list(admin.get("user_ids", []), "admin.user_ids", errors)
    _validate_id_list(notifications.get("admin_user_ids", []), "notifications.admin_user_ids", errors)
    _validate_bots(config.get("bots", []), errors)
    _validate_relay(config.get("relay", {}), errors)

    if errors:
        raise RuntimeError("Invalid config.json:\n- " + "\n- ".join(errors))


def _validate_bots(bots: object, errors: list[str]) -> None:
    if bots in (None, []):
        return
    if not isinstance(bots, list):
        errors.append("bots must be an array.")
        return

    seen_ids: set[str] = set()
    relay_profiles: list[str] = []
    for index, bot in enumerate(bots):
        path = f"bots[{index}]"
        if not isinstance(bot, dict):
            errors.append(f"{path} must be an object.")
            continue

        profile_id = str(bot.get("id", "")).strip()
        if not profile_id:
            errors.append(f"{path}.id is required.")
        elif profile_id in seen_ids:
            errors.append(f"{path}.id duplicates profile id '{profile_id}'.")
        seen_ids.add(profile_id)

        token_env = str(bot.get("token_env", "")).strip()
        if not token_env:
            errors.append(f"{path}.token_env is required.")

        features = bot.get("features", {})
        if not isinstance(features, dict):
            errors.append(f"{path}.features must be an object.")
            continue

        unknown = sorted(set(features) - KNOWN_FEATURES)
        if unknown:
            errors.append(f"{path}.features has unknown feature(s): {', '.join(unknown)}.")
        for name, enabled in features.items():
            if not isinstance(enabled, bool):
                errors.append(f"{path}.features.{name} must be true or false.")
        if features.get("relay") is True:
            relay_profiles.append(profile_id or path)

    if len(relay_profiles) > 1:
        errors.append("Only one bot profile may enable relay: " + ", ".join(relay_profiles) + ".")


def _validate_relay(relay: object, errors: list[str]) -> None:
    if relay in (None, {}):
        return
    if not isinstance(relay, dict):
        errors.append("relay must be an object.")
        return

    groups = relay.get("groups", [])
    if not isinstance(groups, list):
        errors.append("relay.groups must be an array.")
        return

    group_names: set[str] = set()
    for group_index, group in enumerate(groups):
        path = f"relay.groups[{group_index}]"
        if not isinstance(group, dict):
            errors.append(f"{path} must be an object.")
            continue

        name = str(group.get("name", "")).strip()
        if not name:
            errors.append(f"{path}.name is required.")
        elif name in group_names:
            errors.append(f"{path}.name duplicates relay group '{name}'.")
        group_names.add(name)

        if "role_mappings" in group:
            errors.append(f"{path}.role_mappings is no longer supported; use relay.role_mappings.")

        channels = group.get("channels", [])
        if not isinstance(channels, list) or not channels:
            errors.append(f"{path}.channels must be a non-empty array.")
            continue
        for channel_index, channel in enumerate(channels):
            _validate_channel(channel, f"{path}.channels[{channel_index}]", errors)

    _validate_role_mappings(relay.get("role_mappings", []), group_names, errors)


def _validate_channel(channel: object, path: str, errors: list[str]) -> None:
    if not isinstance(channel, dict):
        errors.append(f"{path} must be an object.")
        return
    if not str(channel.get("channel_id", "")).strip():
        errors.append(f"{path}.channel_id is required.")
    direction = str(channel.get("direction", "BOTH")).upper()
    if direction not in VALID_DIRECTIONS:
        errors.append(f"{path}.direction must be one of: {', '.join(sorted(VALID_DIRECTIONS))}.")


def _validate_role_mappings(mappings: object, group_names: set[str], errors: list[str]) -> None:
    if mappings in (None, []):
        return
    if not isinstance(mappings, list):
        errors.append("relay.role_mappings must be an array.")
        return

    require_group_name = len(group_names) > 1
    for index, mapping in enumerate(mappings):
        path = f"relay.role_mappings[{index}]"
        if not isinstance(mapping, dict):
            errors.append(f"{path} must be an object.")
            continue
        group_name = str(mapping.get("group_name", "")).strip()
        if require_group_name and not group_name:
            errors.append(f"{path}.group_name is required when multiple relay groups exist.")
        if group_name and group_name not in group_names:
            errors.append(f"{path}.group_name references unknown relay group '{group_name}'.")
        for key in ("guild_id", "role_id", "common_name"):
            if not str(mapping.get(key, "")).strip():
                errors.append(f"{path}.{key} is required.")


def _validate_id_list(value: object, path: str, errors: list[str]) -> None:
    if value in (None, []):
        return
    if not isinstance(value, list):
        errors.append(f"{path} must be an array.")
        return
    for index, item in enumerate(value):
        if not str(item).strip().isdigit():
            errors.append(f"{path}[{index}] must be a Discord user id.")


def _object_or_empty(value: object, path: str, errors: list[str]) -> dict:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object.")
        return {}
    return value