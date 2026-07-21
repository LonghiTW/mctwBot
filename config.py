"""Environment variable loader."""
import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
CLIENT_ID: str = os.getenv("CLIENT_ID", "")
RELAY_QUEUE_DELAY_MS: int = int(os.getenv("RELAY_QUEUE_DELAY_MS", "600"))
CONFIG_PATH: str = os.getenv("CONFIG_PATH", "config.json")


def validate() -> None:
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not CLIENT_ID:
        missing.append("CLIENT_ID")
    if missing:
        raise RuntimeError(
            f"Missing: {', '.join(missing)}. Copy .env.example to .env."
        )
