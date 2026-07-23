"""Environment variable loader."""
import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
RELAY_QUEUE_DELAY_MS: int = int(os.getenv("RELAY_QUEUE_DELAY_MS", "600"))
CONFIG_PATH: str = os.getenv("CONFIG_PATH", "config.json")
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/database.db")


def validate() -> None:
    """Legacy single-bot validation.

    Multi-profile startup validates token/client env vars via bot_profiles.py.
    """
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if missing:
        raise RuntimeError(
            f"Missing: {', '.join(missing)}. Copy .env.example to .env."
        )
