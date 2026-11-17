"""Environment variable loader."""
import os
from dotenv import load_dotenv

load_dotenv()

RELAY_QUEUE_DELAY_MS: int = int(os.getenv("RELAY_QUEUE_DELAY_MS", "600"))
RELAY_QUEUE_WORKERS: int = int(os.getenv("RELAY_QUEUE_WORKERS", "4"))
CONFIG_PATH: str = os.getenv("CONFIG_PATH", "config.json")
GUILD_CONFIG_DIR: str = os.getenv("GUILD_CONFIG_DIR", "config.guilds")
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/database.db")
PRIVACY_POLICY_URL: str = os.getenv("PRIVACY_POLICY_URL", "").strip()
