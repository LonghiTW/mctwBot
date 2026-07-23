"""Bot profile loading for single-process multi-token startup."""
from dataclasses import dataclass
import os


DEFAULT_FEATURES = {
    "relay": False,
    "keywords": False,
    "scheduler": False,
    "moderation": False,
    "commands": False,
    "admin": False,
}


@dataclass(frozen=True)
class BotProfile:
    id: str
    token_env: str
    command_prefix: str
    features: dict

    @property
    def token(self) -> str:
        return os.getenv(self.token_env, "")


def load_bot_profiles(config: dict) -> list[BotProfile]:
    configured = config.get("bots")
    if configured:
        return [_profile_from_config(item, config) for item in configured]

    features = {**DEFAULT_FEATURES, "relay": True}
    return [
        BotProfile(
            id="default",
            token_env="DISCORD_TOKEN",
            command_prefix="!",
            features=features,
        )
    ]


def validate_bot_profiles(profiles: list[BotProfile]) -> None:
    if not profiles:
        raise RuntimeError("No bot profiles configured.")

    missing = []
    relay_profiles = []
    seen_ids = set()
    for profile in profiles:
        if profile.id in seen_ids:
            raise RuntimeError(f"Duplicate bot profile id: {profile.id}")
        seen_ids.add(profile.id)

        if not profile.token:
            missing.append(profile.token_env)
        if profile.features.get("relay", False):
            relay_profiles.append(profile.id)

    if missing:
        names = ", ".join(sorted(set(missing)))
        raise RuntimeError(f"Missing: {names}. Copy .env.example to .env.")

    if len(relay_profiles) > 1:
        names = ", ".join(relay_profiles)
        raise RuntimeError(f"Only one bot profile may enable relay: {names}")


def _profile_from_config(item: dict, root_config: dict) -> BotProfile:
    profile_id = str(item.get("id", "")).strip()
    if not profile_id:
        raise RuntimeError("Every bot profile must have an id.")

    features = {**DEFAULT_FEATURES, **item.get("features", {})}
    return BotProfile(
        id=profile_id,
        token_env=str(item.get("token_env", "BOT_TOKEN")).strip(),
        command_prefix=str(item.get("command_prefix", "!")),
        features=features,
    )