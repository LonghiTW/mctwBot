from types import SimpleNamespace
import asyncio
import unittest

from cogs.relay.emoji_resolver import EmojiResolver, _cache_name, _emoji_slots_remaining


class EmojiResolverHelperTests(unittest.TestCase):
    def test_cache_name_avoids_double_relay_prefix(self):
        self.assertEqual(
            _cache_name("relay_lul_078804", "1531637703165345852"),
            "relay_lul_345852",
        )

    def test_cache_name_keeps_existing_cache_name_shape(self):
        self.assertEqual(
            _cache_name("relay_lul_345852", "1531637703165345852"),
            "relay_lul_345852",
        )

    def test_cache_name_sanitizes_blank_source_name(self):
        self.assertEqual(_cache_name("!!!", "123456789012345678"), "relay_emoji_345678")

    def test_emoji_slots_remaining_counts_by_animation_type(self):
        guild = SimpleNamespace(
            emoji_limit=5,
            emojis=[
                SimpleNamespace(animated=False),
                SimpleNamespace(animated=False),
                SimpleNamespace(animated=True),
            ],
        )

        self.assertEqual(_emoji_slots_remaining(guild, animated=False), 3)
        self.assertEqual(_emoji_slots_remaining(guild, animated=True), 4)

    def test_resolve_content_skips_cache_guild_emoji(self):
        emoji = SimpleNamespace(id=123456789012345678, animated=False)
        guild = SimpleNamespace(emojis=[])
        bot = SimpleNamespace(get_guild=lambda guild_id: SimpleNamespace(emojis=[emoji]))
        resolver = EmojiResolver(bot)
        resolver._is_cache_guild_emoji = lambda emoji_id: emoji_id == "123456789012345678"

        async def fail_if_called(*args, **kwargs):
            raise AssertionError("ensure_cached should not be called for cache guild emoji")

        resolver.ensure_cached = fail_if_called

        content, embeds = asyncio.run(resolver.resolve_content("<:relay_lul_345678:123456789012345678>", [], guild))

        self.assertEqual(content, "<:relay_lul_345678:123456789012345678>")
        self.assertEqual(embeds, [])


if __name__ == "__main__":
    unittest.main()
