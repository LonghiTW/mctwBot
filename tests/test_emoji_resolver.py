from types import SimpleNamespace
import asyncio
import unittest

from cogs.relay.emoji_resolver import EmojiResolver, _cache_name, _emoji_slots_remaining
import cogs.relay.emoji_resolver as emoji_resolver


class _FakeDb:
    def __init__(self):
        self.rows = [
            {"source_emoji_id": "source-active", "cached_emoji_id": "1", "cached_name": "relay_keep_000001"},
            {"source_emoji_id": "source-stale", "cached_emoji_id": "2", "cached_name": "relay_stale_000002"},
        ]
        self.deleted_sources = []
        self.commits = 0

    def fetchall(self, query, params=()):
        return list(self.rows)

    def execute(self, query, params=()):
        self.deleted_sources.append(params[0])
        self.rows = [row for row in self.rows if row["source_emoji_id"] != params[0]]
        return None

    def commit(self):
        self.commits += 1


class _FakeEmoji:
    def __init__(self, emoji_id, name):
        self.id = emoji_id
        self.name = name
        self.deleted = False

    async def delete(self, reason=None):
        self.deleted = True


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

    def test_sync_cache_index_removes_stale_rows_and_orphans(self):
        active = _FakeEmoji(1, "relay_keep_000001")
        orphan = _FakeEmoji(3, "relay_orphan_000003")
        manual = _FakeEmoji(4, "manual")
        guild = SimpleNamespace(
            id=99,
            emojis=[active, orphan, manual],
            me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_expressions=True)),
        )
        bot = SimpleNamespace(get_guild=lambda guild_id: guild)
        fake_db = _FakeDb()
        resolver = EmojiResolver(bot)

        original_load_config = emoji_resolver.load_config
        original_database_manager = emoji_resolver.DatabaseManager
        try:
            emoji_resolver.load_config = lambda: {"relay": {"emoji_cache_guild_id": "99"}}
            emoji_resolver.DatabaseManager = lambda: fake_db

            asyncio.run(resolver.sync_cache_index())
        finally:
            emoji_resolver.load_config = original_load_config
            emoji_resolver.DatabaseManager = original_database_manager

        self.assertEqual(fake_db.deleted_sources, ["source-stale"])
        self.assertTrue(orphan.deleted)
        self.assertFalse(active.deleted)
        self.assertFalse(manual.deleted)


if __name__ == "__main__":
    unittest.main()
