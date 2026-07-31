import asyncio
import unittest
from types import SimpleNamespace

from cogs.relay.reactions import ReactionSync
import cogs.relay.reactions as reactions


class _FakeDb:
    def fetchall(self, query, params=()):
        return [
            {"relayed_message_id": "200", "relayed_channel_id": "20"},
            {"relayed_message_id": "300", "relayed_channel_id": "30"},
        ]

    def fetchone(self, query, params=()):
        return None


class _FakeReaction:
    def __init__(self, emoji, count, me):
        self.emoji = emoji
        self.count = count
        self.me = me


class _FakeMessage:
    def __init__(self, reactions=None):
        self.reactions = list(reactions) if reactions else []
        self.removed = []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def remove_reaction(self, emoji, member):
        self.removed.append(emoji)


class _FakeChannel:
    def __init__(self, guild, message=None):
        self.guild = guild
        self.message = message if message is not None else _FakeMessage()

    async def fetch_message(self, message_id):
        return self.message


class ReactionSyncTests(unittest.TestCase):
    def test_resolves_reaction_for_each_target_guild(self):
        guild_a = SimpleNamespace(id=2)
        guild_b = SimpleNamespace(id=3)
        channels = {
            20: _FakeChannel(guild_a),
            30: _FakeChannel(guild_b),
        }
        bot = SimpleNamespace(
            get_channel=lambda channel_id: channels[channel_id],
            user=SimpleNamespace(id=999),
        )
        seen_guild_ids = []

        async def resolve_emoji(emoji, target_guild):
            seen_guild_ids.append(target_guild.id)
            return f"resolved-{target_guild.id}"

        payload = SimpleNamespace(
            message_id=100,
            channel_id=10,
            emoji=SimpleNamespace(id=123, name="panda", animated=False),
        )

        original_database_manager = reactions.DatabaseManager
        try:
            reactions.DatabaseManager = lambda: _FakeDb()
            asyncio.run(ReactionSync(bot, resolve_emoji).sync(payload, add=True))
        finally:
            reactions.DatabaseManager = original_database_manager

        self.assertEqual(set(seen_guild_ids), {2, 3})
        self.assertEqual(channels[20].message.reactions, ["resolved-2"])
        self.assertEqual(channels[30].message.reactions, ["resolved-3"])

    def test_reaction_on_relayed_copy_fans_out_to_original_and_siblings(self):
        # relayed_messages: 100 (original) -> 200/20, 300/30, 400/40
        class _Db:
            def fetchall(self, query, params=()):
                if params == ("200",):
                    return []
                return [
                    {"relayed_message_id": "200", "relayed_channel_id": "20"},
                    {"relayed_message_id": "300", "relayed_channel_id": "30"},
                    {"relayed_message_id": "400", "relayed_channel_id": "40"},
                ]

            def fetchone(self, query, params=()):
                return {
                    "original_message_id": "100",
                    "original_channel_id": "10",
                }

        channels = {
            10: _FakeChannel(SimpleNamespace(id=1)),
            20: _FakeChannel(SimpleNamespace(id=2)),
            30: _FakeChannel(SimpleNamespace(id=3)),
            40: _FakeChannel(SimpleNamespace(id=4)),
        }
        bot = SimpleNamespace(
            get_channel=lambda channel_id: channels[channel_id],
            user=SimpleNamespace(id=999),
        )

        async def resolve_emoji(emoji, target_guild):
            return f"resolved-{target_guild.id}"

        payload = SimpleNamespace(
            message_id=200,  # reaction landed on the B copy
            channel_id=20,
            emoji=SimpleNamespace(id=123, name="panda", animated=False),
        )

        original_database_manager = reactions.DatabaseManager
        try:
            reactions.DatabaseManager = lambda: _Db()
            asyncio.run(ReactionSync(bot, resolve_emoji).sync(payload, add=True))
        finally:
            reactions.DatabaseManager = original_database_manager

        # B itself is excluded; original (10) and siblings (30, 40) all receive it
        self.assertEqual(channels[10].message.reactions, ["resolved-1"])
        self.assertEqual(channels[20].message.reactions, [])
        self.assertEqual(channels[30].message.reactions, ["resolved-3"])
        self.assertEqual(channels[40].message.reactions, ["resolved-4"])

    def test_remove_keeps_reaction_while_other_users_still_have_it(self):
        emoji = SimpleNamespace(id=123, name="panda", animated=False)

        class _Db:
            def fetchall(self, query, params=()):
                return [
                    {"relayed_message_id": "200", "relayed_channel_id": "20"},
                    {"relayed_message_id": "300", "relayed_channel_id": "30"},
                ]

            def fetchone(self, query, params=()):
                return None

        msg10 = _FakeMessage([_FakeReaction(emoji, count=1, me=True)])  # only bot left
        msg20 = _FakeMessage([_FakeReaction(emoji, count=2, me=True)])  # bot + one user
        msg30 = _FakeMessage([_FakeReaction(emoji, count=1, me=True)])  # only bot left
        channels = {
            10: _FakeChannel(SimpleNamespace(id=1), msg10),
            20: _FakeChannel(SimpleNamespace(id=2), msg20),
            30: _FakeChannel(SimpleNamespace(id=3), msg30),
        }
        bot = SimpleNamespace(
            get_channel=lambda channel_id: channels[channel_id],
            user=SimpleNamespace(id=999),
        )

        async def resolve_emoji(emoji, target_guild):
            return emoji

        payload = SimpleNamespace(message_id=100, channel_id=10, emoji=emoji)

        original_database_manager = reactions.DatabaseManager
        try:
            reactions.DatabaseManager = lambda: _Db()
            asyncio.run(ReactionSync(bot, resolve_emoji).sync(payload, add=False))
        finally:
            reactions.DatabaseManager = original_database_manager

        # Channel 20 still has a real user reacted — nothing is removed anywhere.
        self.assertEqual(msg10.removed, [])
        self.assertEqual(msg20.removed, [])
        self.assertEqual(msg30.removed, [])

    def test_remove_clears_reactions_when_no_user_has_emoji_anymore(self):
        emoji = SimpleNamespace(id=123, name="panda", animated=False)

        class _Db:
            def fetchall(self, query, params=()):
                return [
                    {"relayed_message_id": "200", "relayed_channel_id": "20"},
                    {"relayed_message_id": "300", "relayed_channel_id": "30"},
                ]

            def fetchone(self, query, params=()):
                return None

        msg10 = _FakeMessage([_FakeReaction(emoji, count=1, me=True)])  # only bot
        msg20 = _FakeMessage([])                                        # nobody
        msg30 = _FakeMessage([_FakeReaction(emoji, count=1, me=True)])  # only bot
        channels = {
            10: _FakeChannel(SimpleNamespace(id=1), msg10),
            20: _FakeChannel(SimpleNamespace(id=2), msg20),
            30: _FakeChannel(SimpleNamespace(id=3), msg30),
        }
        bot = SimpleNamespace(
            get_channel=lambda channel_id: channels[channel_id],
            user=SimpleNamespace(id=999),
        )

        async def resolve_emoji(emoji, target_guild):
            return emoji

        payload = SimpleNamespace(message_id=100, channel_id=10, emoji=emoji)

        original_database_manager = reactions.DatabaseManager
        try:
            reactions.DatabaseManager = lambda: _Db()
            asyncio.run(ReactionSync(bot, resolve_emoji).sync(payload, add=False))
        finally:
            reactions.DatabaseManager = original_database_manager

        # Everyone removed — the bot's synced reaction is torn down on all
        # copies, including the event channel itself.
        self.assertEqual(msg10.removed, [emoji])
        self.assertEqual(msg20.removed, [emoji])
        self.assertEqual(msg30.removed, [emoji])


if __name__ == "__main__":
    unittest.main()