import json
import tempfile
import unittest
from pathlib import Path

from app.guild_config import GuildConfigError, GuildConfigManager, validate_guild_config


class GuildConfigTests(unittest.TestCase):
    def test_ensure_guild_creates_default_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = GuildConfigManager(temp_dir)

            config = manager.ensure_guild("123456789012345678")

            self.assertEqual(config["guild_id"], "123456789012345678")
            self.assertTrue((Path(temp_dir) / "123456789012345678.json").exists())

    def test_feature_and_module_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "123.json"
            path.write_text(json.dumps({
                "guild_id": "123",
                "features": {"keywords": True},
                "keywords": {"hello": {"enabled": False}},
            }), encoding="utf-8")
            manager = GuildConfigManager(temp_dir)

            self.assertTrue(manager.feature_enabled("123", "keywords"))
            self.assertFalse(manager.module_enabled("123", "keywords", "hello"))
            self.assertFalse(manager.module_enabled("123", "moderation", "welcome_cleaner"))

    def test_channels_for_requires_enabled_module(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "123.json"
            path.write_text(json.dumps({
                "guild_id": "123",
                "features": {"scheduler": True},
                "scheduler": {"friday_night": {"enabled": True, "channels": ["456", 789]}},
            }), encoding="utf-8")
            manager = GuildConfigManager(temp_dir)

            self.assertEqual(manager.channels_for("123", "scheduler", "friday_night"), [456, 789])

    def test_validate_rejects_invalid_channel_ids(self):
        with self.assertRaises(GuildConfigError):
            validate_guild_config({
                "guild_id": "123",
                "features": {"moderation": True},
                "moderation": {"welcome_cleaner": {"enabled": True, "channels": ["abc"]}},
            })


if __name__ == "__main__":
    unittest.main()