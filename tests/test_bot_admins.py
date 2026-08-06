"""Tests for the app.bot_admins feature-node helpers."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.bot_admins import (
    KNOWN_FEATURES,
    bot_admin_has_feature,
    bot_admin_ids_with_feature,
    is_bot_admin,
    load_config,
)


def _write_config(temp_dir: str, config: dict) -> str:
    path = Path(temp_dir) / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


class BotAdminsTests(unittest.TestCase):
    def test_known_features(self):
        self.assertEqual(
            KNOWN_FEATURES,
            {"exclusive_command", "notifications", "relay_reverse_delete"},
        )

    def test_new_schema_feature_nodes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = _write_config(temp_dir, {
                "bot_admins": [
                    {
                        "id": "111",
                        "name": "Alice",
                        "features": {
                            "exclusive_command": True,
                            "notifications": False,
                            "relay_reverse_delete": True,
                        },
                    },
                    {"id": "222", "features": {"notifications": True}},
                ],
            })
            with patch("app.bot_admins.CONFIG_PATH", config_path):
                self.assertTrue(bot_admin_has_feature(111, "exclusive_command"))
                self.assertFalse(bot_admin_has_feature(111, "notifications"))
                self.assertTrue(bot_admin_has_feature("111", "relay_reverse_delete"))
                self.assertFalse(bot_admin_has_feature(222, "exclusive_command"))
                self.assertTrue(bot_admin_has_feature(222, "notifications"))
                self.assertFalse(bot_admin_has_feature(333, "exclusive_command"))

                self.assertTrue(is_bot_admin("111"))
                self.assertTrue(is_bot_admin(222))
                self.assertFalse(is_bot_admin(333))

                self.assertEqual(bot_admin_ids_with_feature("relay_reverse_delete"), {111})
                self.assertEqual(bot_admin_ids_with_feature("notifications"), {222})

    def test_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = _write_config(temp_dir, {
                "admin": {"user_ids": ["111"]},
                "notifications": {"admin_user_ids": ["222", 333]},
            })
            with patch("app.bot_admins.CONFIG_PATH", config_path):
                self.assertTrue(bot_admin_has_feature("111", "exclusive_command"))
                self.assertTrue(bot_admin_has_feature("222", "notifications"))
                self.assertTrue(bot_admin_has_feature(333, "notifications"))
                self.assertFalse(bot_admin_has_feature("111", "notifications"))

                self.assertTrue(is_bot_admin("111"))
                self.assertFalse(is_bot_admin("999"))

                self.assertEqual(bot_admin_ids_with_feature("exclusive_command"), {111})
                self.assertEqual(bot_admin_ids_with_feature("notifications"), {222, 333})

    def test_new_schema_overrides_legacy_for_same_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = _write_config(temp_dir, {
                "admin": {"user_ids": ["111"]},
                "bot_admins": [{"id": "111", "features": {"notifications": True}}],
            })
            with patch("app.bot_admins.CONFIG_PATH", config_path):
                self.assertTrue(bot_admin_has_feature(111, "notifications"))
                self.assertFalse(bot_admin_has_feature(111, "exclusive_command"))

    def test_missing_config_no_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = str(Path(temp_dir) / "does_not_exist.json")
            with patch("app.bot_admins.CONFIG_PATH", missing_path):
                self.assertEqual(load_config(), {})
                self.assertFalse(is_bot_admin(111))
                self.assertFalse(bot_admin_has_feature(111, "exclusive_command"))
                self.assertEqual(bot_admin_ids_with_feature("notifications"), set())

    def test_invalid_json_no_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text("not json at all", encoding="utf-8")
            with patch("app.bot_admins.CONFIG_PATH", str(path)):
                self.assertEqual(load_config(), {})
                self.assertFalse(is_bot_admin(1))


if __name__ == "__main__":
    unittest.main()
