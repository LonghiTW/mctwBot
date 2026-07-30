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


class _FakeMessage:
    def __init__(self):
        self.reactions = []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


class _FakeChannel:
    def __init__(self, guild):
        self.guild = guild
        self.message = _FakeMessage()

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

        self.assertEqual(seen_guild_ids, [2, 3])
        self.assertEqual(channels[20].message.reactions, ["resolved-2"])
        self.assertEqual(channels[30].message.reactions, ["resolved-3"])


if __name__ == "__main__":
    unittest.main()