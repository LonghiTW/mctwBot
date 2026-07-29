import unittest
from types import SimpleNamespace

from cogs.relay.admin_views import RelayAdminViews, _strip_emoji


class _Bot:
    def get_guild(self, guild_id):
        return SimpleNamespace(name="🐼 Panda Server ✨")

    def get_channel(self, channel_id):
        return None


class RelayAdminViewsTests(unittest.TestCase):
    def test_strip_emoji_removes_common_emoji(self):
        self.assertEqual(_strip_emoji("🐼 Panda Server ✨"), "Panda Server")

    def test_format_channel_link_uses_clean_guild_name(self):
        views = RelayAdminViews(_Bot())

        self.assertEqual(
            views.format_channel_link("123", "456"),
            "[Panda Server](https://discord.com/channels/123/456)",
        )

    def test_format_channel_link_falls_back_when_name_is_only_emoji(self):
        class EmojiOnlyBot(_Bot):
            def get_guild(self, guild_id):
                return SimpleNamespace(name="🐼✨")

        views = RelayAdminViews(EmojiOnlyBot())

        self.assertEqual(
            views.format_channel_link("123", "456"),
            "[123](https://discord.com/channels/123/456)",
        )

    def test_reload_welcome_uses_vertical_channel_list(self):
        views = RelayAdminViews(_Bot())

        _updates, welcomes = views.build_reload_notifications(
            [],
            [
                {"group_name": "main", "channel_id": "456", "guild_id": "123"},
                {"group_name": "main", "channel_id": "789", "guild_id": "123"},
            ],
        )

        self.assertEqual(
            welcomes[0][1],
            "👋 此頻道已加入麥塊聯盟的群組 **main**。\n"
            "群組內的其他頻道：\n"
            "- [Panda Server](https://discord.com/channels/123/789)",
        )


if __name__ == "__main__":
    unittest.main()
