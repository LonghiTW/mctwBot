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
    _interaction_check,
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
        self.root = self.cog.__cog_app_commands__[0]

    # ------------------------------------------------------------------
    # Permission gate wiring on the subgroup bindings
    # ------------------------------------------------------------------
    def test_group_subcommand_binding_runs_permission_check(self):
        add_cmd = self.root._children["group"]._children["add"]

        with patch(
            "cogs.bot_admin.relay_admin_commands.bot_admin_has_feature",
            return_value=True,
        ):
            ok = asyncio.run(add_cmd._check_can_run(_interaction()))

        self.assertTrue(ok)

    def test_group_subcommand_denies_non_admin(self):
        add_cmd = self.root._children["group"]._children["add"]
        interaction = _interaction()

        with patch(
            "cogs.bot_admin.relay_admin_commands.bot_admin_has_feature",
            return_value=False,
        ):
            ok = asyncio.run(add_cmd._check_can_run(interaction))

        self.assertFalse(ok)
        interaction.response.send_message.assert_called_once()

    def test_channel_subcommand_binding_runs_permission_check(self):
        add_cmd = self.root._children["channel"]._children["add"]

        with patch(
            "cogs.bot_admin.relay_admin_commands.bot_admin_has_feature",
            return_value=True,
        ):
            ok = asyncio.run(add_cmd._check_can_run(_interaction()))

        self.assertTrue(ok)

    # ------------------------------------------------------------------
    # _interaction_check itself
    # ------------------------------------------------------------------
    def test_interaction_check_allows_exclusive_command_admin(self):
        with patch(
            "cogs.bot_admin.relay_admin_commands.bot_admin_has_feature",
            return_value=True,
        ):
            result = asyncio.run(_interaction_check(_interaction()))

        self.assertTrue(result)

    def test_interaction_check_denies_non_admin(self):
        with patch(
            "cogs.bot_admin.relay_admin_commands.bot_admin_has_feature",
            return_value=False,
        ):
            result = asyncio.run(_interaction_check(_interaction()))

        self.assertFalse(result)

    # ------------------------------------------------------------------
    # Channel kind classification
    # ------------------------------------------------------------------
    def test_channel_kind_classifies_text_and_forum(self):
        self.assertEqual(_channel_kind(FakeTextChannel()), "text")
        self.assertEqual(_channel_kind(FakeForumChannel()), "forum")

    # ------------------------------------------------------------------
    # Group autocomplete
    # ------------------------------------------------------------------
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
