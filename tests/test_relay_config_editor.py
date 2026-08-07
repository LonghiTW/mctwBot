import tempfile
import unittest
from pathlib import Path

from app.config_validator import validate_config
from app.relay_config_editor import (
    RelayConfigEditError,
    add_channel,
    add_group,
    edit_channel,
    edit_group,
    group_names,
    remove_channel,
    remove_group,
    save_config_file,
)


def _base_config():
    return {
        "bot_admins": [],
        "bots": [],
        "relay": {
            "groups": [
                {
                    "name": "main",
                    "hidden": False,
                    "channel_kind": "text",
                    "channels": [
                        {
                            "channel_id": "111",
                            "direction": "BOTH",
                            "process_bot_messages": False,
                            "allow_forward_delete": True,
                            "allow_reverse_delete": False,
                        }
                    ],
                }
            ],
            "role_mappings": [
                {"group_name": "main", "guild_id": "1", "role_id": "2", "common_name": "K30"}
            ],
        },
    }


class RelayConfigEditorTests(unittest.TestCase):
    def test_add_group_allows_empty_group(self):
        config = add_group(_base_config(), "ops", hidden=True)

        self.assertIn("ops", group_names(config))
        self.assertEqual(config["relay"]["groups"][1], {"name": "ops", "hidden": True, "channels": []})
        validate_config(config)

    def test_add_group_rejects_duplicate(self):
        with self.assertRaisesRegex(RelayConfigEditError, "already exists"):
            add_group(_base_config(), "main")

    def test_edit_group_renames_role_mappings(self):
        config = edit_group(_base_config(), "main", new_name="global", hidden=True)

        group = config["relay"]["groups"][0]
        self.assertEqual(group["name"], "global")
        self.assertTrue(group["hidden"])
        self.assertEqual(config["relay"]["role_mappings"][0]["group_name"], "global")

    def test_remove_group_removes_role_mappings_without_confirm(self):
        config, removed = remove_group(_base_config(), "main")

        self.assertEqual(removed["name"], "main")
        self.assertEqual(config["relay"]["groups"], [])
        self.assertEqual(config["relay"]["role_mappings"], [])
        validate_config(config)

    def test_add_channel_defaults_and_duplicate_guard(self):
        config = add_group(_base_config(), "ops")
        config = add_channel(config, "ops", 222, "text")
        channel = config["relay"]["groups"][1]["channels"][0]

        self.assertEqual(channel["channel_id"], "222")
        self.assertEqual(channel["direction"], "BOTH")
        self.assertFalse(channel["process_bot_messages"])
        self.assertTrue(channel["allow_forward_delete"])
        self.assertFalse(channel["allow_reverse_delete"])
        self.assertEqual(config["relay"]["groups"][1]["channel_kind"], "text")

        with self.assertRaisesRegex(RelayConfigEditError, "already exists"):
            add_channel(config, "ops", "222", "text")

    def test_add_channel_rejects_mixed_group_channel_kind(self):
        with self.assertRaisesRegex(RelayConfigEditError, "same channel type"):
            add_channel(_base_config(), "main", 333, "forum")

    def test_edit_channel_updates_and_moves(self):
        config = add_group(_base_config(), "ops")
        config, old_group, channel = edit_channel(
            config,
            111,
            "text",
            group_name="ops",
            direction="send_only",
            brand_name="Road",
            process_bot_messages=True,
            allow_forward_delete=False,
            allow_reverse_delete=True,
        )

        self.assertEqual(old_group, "main")
        self.assertEqual(channel["channel_id"], "111")
        moved = config["relay"]["groups"][1]["channels"][0]
        self.assertEqual(moved["direction"], "SEND_ONLY")
        self.assertEqual(moved["brand_name"], "Road")
        self.assertTrue(moved["process_bot_messages"])
        self.assertFalse(moved["allow_forward_delete"])
        self.assertTrue(moved["allow_reverse_delete"])

    def test_edit_channel_clear_brand_name(self):
        config, _old_group, channel = edit_channel(
            _base_config(),
            111,
            "text",
            brand_name="Road",
        )
        self.assertEqual(channel["brand_name"], "Road")

        config, _old_group, channel = edit_channel(config, 111, "text", clear_brand_name=True)
        self.assertEqual(channel["brand_name"], "")

        with self.assertRaisesRegex(RelayConfigEditError, "cannot be used together"):
            edit_channel(config, 111, "text", brand_name="A", clear_brand_name=True)

    def test_remove_channel_without_confirm_keeps_empty_group(self):
        config, group_name, channel = remove_channel(_base_config(), 111)

        self.assertEqual(group_name, "main")
        self.assertEqual(channel["channel_id"], "111")
        self.assertEqual(config["relay"]["groups"][0]["channels"], [])
        validate_config(config)

    def test_save_config_file_is_atomic_and_validates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            config = add_group({"relay": {"groups": [], "role_mappings": []}}, "main")

            save_config_file(config, path)

            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
