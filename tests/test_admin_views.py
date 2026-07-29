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


if __name__ == "__main__":
    unittest.main()
