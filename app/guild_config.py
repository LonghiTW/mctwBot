"""Per-guild feature configuration loading and generation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import discord

from app.config import GUILD_CONFIG_DIR


DEFAULT_GUILD_CONFIG = {
    "guild_id": "",
    "features": {
        "keywords": False,
        "scheduler": False,
        "moderation": False,
    },
    "keywords": {
        "hello": {"enabled": True},
        "birthday": {"enabled": True},
    },
    "moderation": {
        "welcome_cleaner": {"enabled": True, "channels": []},
    },
    "scheduler": {
        "friday_night": {"enabled": True, "channels": []},
        "sunday_night": {"enabled": True, "channels": []},
    },
}


class GuildConfigError(RuntimeError):
    pass


class GuildConfigManager:
    def __init__(self, directory: str | Path | None = None):
        self.directory = _resolve_directory(directory or GUILD_CONFIG_DIR)
        self._cache: dict[str, dict[str, Any]] = {}

    def reload(self) -> None:
        self._cache.clear()

    def ensure_guild(self, guild: discord.Guild | int | str) -> dict[str, Any]:
        guild_id = _guild_id(guild)
        path = self._path_for(guild_id)
        if not path.exists():
            config = self._default_for(guild_id)
            self._atomic_write(path, config)
            self._cache[guild_id] = config
            return copy.deepcopy(config)
        return self.get(guild_id)

    def ensure_all(self, guilds) -> None:
        for guild in guilds:
            self.ensure_guild(guild)

    def get(self, guild: discord.Guild | int | str) -> dict[str, Any]:
        guild_id = _guild_id(guild)
        if guild_id in self._cache:
            return copy.deepcopy(self._cache[guild_id])

        path = self._path_for(guild_id)
        if not path.exists():
            return self.ensure_guild(guild_id)

        with path.open("r", encoding="utf-8") as file:
            config = json.load(file)
        validate_guild_config(config, path)
        self._cache[guild_id] = config
        return copy.deepcopy(config)

    def feature_enabled(self, guild: discord.Guild | int | str, feature: str, default: bool = False) -> bool:
        config = self.get(guild)
        features = config.get("features", {})
        if not isinstance(features, dict):
            return default
        return bool(features.get(feature, default))

    def module_enabled(
        self,
        guild: discord.Guild | int | str,
        feature: str,
        module: str,
        default: bool = True,
    ) -> bool:
        if not self.feature_enabled(guild, feature):
            return False
        module_config = self.module_config(guild, feature, module)
        return bool(module_config.get("enabled", default))

    def module_config(self, guild: discord.Guild | int | str, feature: str, module: str) -> dict[str, Any]:
        config = self.get(guild)
        feature_config = config.get(feature, {})
        if not isinstance(feature_config, dict):
            return {}
        module_config = feature_config.get(module, {})
        return module_config if isinstance(module_config, dict) else {}

    def channels_for(self, guild: discord.Guild | int | str, feature: str, module: str) -> list[int]:
        if not self.module_enabled(guild, feature, module):
            return []
        channels = self.module_config(guild, feature, module).get("channels", [])
        if not isinstance(channels, list):
            return []
        return [int(channel_id) for channel_id in channels if str(channel_id).strip().isdigit()]

    def enabled_channels_for_bot(self, bot: discord.Client, feature: str, module: str) -> list[int]:
        channel_ids: list[int] = []
        for guild in bot.guilds:
            channel_ids.extend(self.channels_for(guild.id, feature, module))
        return channel_ids

    def _path_for(self, guild_id: str) -> Path:
        return self.directory / f"{guild_id}.json"

    def _default_for(self, guild_id: str) -> dict[str, Any]:
        config = copy.deepcopy(DEFAULT_GUILD_CONFIG)
        config["guild_id"] = guild_id
        return config

    def _atomic_write(self, path: Path, config: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(config, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temp_path.replace(path)


def validate_guild_config(config: object, path: Path | str = "guild config") -> None:
    errors: list[str] = []
    label = str(path)

    if not isinstance(config, dict):
        raise GuildConfigError(f"{label} must contain a JSON object.")

    guild_id = str(config.get("guild_id", "")).strip()
    if not guild_id.isdigit():
        errors.append("guild_id is required and must be a Discord guild id.")

    features = config.get("features", {})
    if not isinstance(features, dict):
        errors.append("features must be an object.")
    else:
        for name, enabled in features.items():
            if not isinstance(enabled, bool):
                errors.append(f"features.{name} must be true or false.")

    for feature in ("keywords", "scheduler", "moderation"):
        feature_config = config.get(feature, {})
        if feature_config in (None, {}):
            continue
        if not isinstance(feature_config, dict):
            errors.append(f"{feature} must be an object.")
            continue
        for module_name, module_config in feature_config.items():
            module_path = f"{feature}.{module_name}"
            if not isinstance(module_config, dict):
                errors.append(f"{module_path} must be an object.")
                continue
            if "enabled" in module_config and not isinstance(module_config["enabled"], bool):
                errors.append(f"{module_path}.enabled must be true or false.")
            if "channels" in module_config:
                channels = module_config["channels"]
                if not isinstance(channels, list):
                    errors.append(f"{module_path}.channels must be an array.")
                else:
                    for index, channel_id in enumerate(channels):
                        if not str(channel_id).strip().isdigit():
                            errors.append(f"{module_path}.channels[{index}] must be a Discord channel id.")

    if errors:
        raise GuildConfigError(f"Invalid {label}:\n- " + "\n- ".join(errors))


def _resolve_directory(directory: str | Path) -> Path:
    path = Path(directory)
    return path if path.is_absolute() else Path.cwd() / path


def _guild_id(guild: discord.Guild | int | str) -> str:
    value = getattr(guild, "id", guild)
    guild_id = str(value).strip()
    if not guild_id.isdigit():
        raise GuildConfigError(f"Invalid guild id: {value}")
    return guild_id


guild_configs = GuildConfigManager()