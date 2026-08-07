"""Tests for the relay slash command cog."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.bot_admin.relay_admin_commands import (
    RelayAdminCommands,
    _channel_kind,
    _group_autocomplete,
)


def _interaction(user_id=111):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


class FakeTextChannel(discord.TextChannel):
    def __init__(self):
        pass


class FakeForumChannel(discord.ForumChannel):
    def __init__(self):
        pass


class RelayAdminCommandsTests(unittest.TestCase):
    def setUp(self):
        self.cog = RelayAdminCommands(SimpleNamespace())

    def test_interaction_check_allows_exclusive_command_admin(self):
        with patch(
            "cogs.bot_admin.relay_admin_commands.bot_admin_has_feature",
            return_value=True,
        ):
            result = asyncio.run(self.cog.interaction_check(_interaction()))

        self.assertTrue(result)

    def test_interaction_check_denies_non_admin(self):
        with patch(
            "cogs.bot_admin.relay_admin_commands.bot_admin_has_feature",
            return_value=False,
        ):
            result = asyncio.run(self.cog.interaction_check(_interaction()))

        self.assertFalse(result)

    def test_channel_kind_classifies_text_and_forum(self):
        self.assertEqual(_channel_kind(FakeTextChannel()), "text")
        self.assertEqual(_channel_kind(FakeForumChannel()), "forum")

    def test_group_autocomplete_returns_matching_names(self):
        async def run():
            with patch(
                "cogs.bot_admin.relay_admin_commands.load_config_file",
                return_value={
                    "relay": {
                        "groups": [
                            {"name": "main"},
                            {"name": "main-ops"},
                            {"name": "ops"},
                        ],
                        "role_mappings": [],
                    }
                },
            ):
                return await _group_autocomplete(_interaction(), "MAIN")

        choices = asyncio.run(run())

        self.assertEqual(
            [choice.value for choice in choices],
            ["main", "main-ops"],
        )

    def test_autocomplete_handles_missing_config(self):
        async def run():
            with patch(
                "cogs.bot_admin.relay_admin_commands.load_config_file",
                side_effect=RuntimeError("missing"),
            ):
                return await _group_autocomplete(_interaction(), "x")

        self.assertEqual(asyncio.run(run()), [])


if __name__ == "__main__":
    unittest.main()
