import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from app.bot_profiles import BotProfile
from cogs.relay.relay_cog import RelayCog
from main import create_intents


class _FakeDb:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def commit(self):
        pass


class PrivacyIntentTests(unittest.TestCase):
    def test_intents_are_limited_to_profile_features(self):
        relay = BotProfile("relay", "TOKEN_RELAY", "!", {"relay": True})
        scheduler = BotProfile("scheduler", "TOKEN_SCHEDULER", "!", {"scheduler": True})
        moderation = BotProfile("moderation", "TOKEN_MOD", "!", {"moderation": True})

        relay_intents = create_intents(relay)
        scheduler_intents = create_intents(scheduler)
        moderation_intents = create_intents(moderation, {"moderation": {"showcase": {"enabled": True}}})
        welcome_only_intents = create_intents(moderation, {"moderation": {"showcase": {"enabled": False}}})

        self.assertTrue(relay_intents.message_content)
        self.assertFalse(relay_intents.members)
        self.assertFalse(relay_intents.presences)
        self.assertFalse(scheduler_intents.message_content)
        self.assertFalse(scheduler_intents.members)
        self.assertTrue(moderation_intents.message_content)
        self.assertTrue(moderation_intents.members)
        self.assertFalse(welcome_only_intents.message_content)
        self.assertTrue(welcome_only_intents.members)

    def test_user_can_opt_out_of_relay(self):
        db = _FakeDb()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        cog = object.__new__(RelayCog)

        with patch("cogs.relay.relay_cog.DatabaseManager", return_value=db):
            asyncio.run(RelayCog.relay_privacy.callback(cog, interaction, False))

        self.assertIn("INSERT OR IGNORE INTO relay_opt_out", db.calls[0][0])
        self.assertEqual(db.calls[0][1], ("42",))
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    def test_user_can_opt_in_again(self):
        db = _FakeDb()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        cog = object.__new__(RelayCog)

        with patch("cogs.relay.relay_cog.DatabaseManager", return_value=db):
            asyncio.run(RelayCog.relay_privacy.callback(cog, interaction, True))

        self.assertIn("DELETE FROM relay_opt_out", db.calls[0][0])
        self.assertEqual(db.calls[0][1], ("42",))

    def test_opted_out_message_is_not_relayed_or_counted_as_thread_activity(self):
        db = _FakeDb()
        db.fetchone = Mock(return_value={"1": 1})
        activity = Mock()
        cog = object.__new__(RelayCog)
        cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
        cog.thread_sync = SimpleNamespace(record_user_activity=activity)

        class FakeThread:
            id = 123

        message = SimpleNamespace(
            guild=SimpleNamespace(id=7),
            type=discord.MessageType.default,
            author=SimpleNamespace(id=42, bot=False),
            webhook_id=None,
            application_id=None,
            channel=FakeThread(),
        )

        with patch("cogs.relay.relay_cog.DatabaseManager", return_value=db), \
                patch("cogs.relay.relay_cog.discord.Thread", FakeThread):
            asyncio.run(RelayCog.on_message(cog, message))

        activity.assert_not_called()
        self.assertEqual(len(db.calls), 0)


if __name__ == "__main__":
    unittest.main()
