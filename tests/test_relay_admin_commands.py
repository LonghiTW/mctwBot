"""Tests for the relay slash command cog."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands

from cogs.bot_admin.relay_admin_commands import (
    RelayAdminCommands,
    RelayChannelTransformer,
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
    # RelayChannelTransformer (DM-friendly channel option)
    # ------------------------------------------------------------------
    def test_transformer_uses_cache_when_available(self):
        async def run():
            channel = FakeTextChannel()
            value = SimpleNamespace(id=1, resolve=lambda: channel)
            return await RelayChannelTransformer().transform(
                SimpleNamespace(client=AsyncMock()), value
            )

        result = asyncio.run(run())

        self.assertIsInstance(result, discord.TextChannel)

    def test_transformer_fetches_when_cache_misses(self):
        """DM scenario: resolve() returns None but fetch_channel succeeds."""
        async def run():
            channel = FakeTextChannel()
            value = SimpleNamespace(id=1, resolve=lambda: None)
            interaction = SimpleNamespace(
                client=SimpleNamespace(fetch_channel=AsyncMock(return_value=channel))
            )
            return await RelayChannelTransformer().transform(interaction, value)

        result = asyncio.run(run())

        self.assertIsInstance(result, discord.TextChannel)

    def test_transformer_rejects_when_fetch_fails(self):
        async def run():
            value = SimpleNamespace(id=999, resolve=lambda: None)
            interaction = SimpleNamespace(
                client=SimpleNamespace(
                    fetch_channel=AsyncMock(side_effect=RuntimeError("missing"))
                )
            )
            return await RelayChannelTransformer().transform(interaction, value)

        with self.assertRaises(app_commands.TransformerError):
            asyncio.run(run())

    def test_transformer_rejects_non_text_channels(self):
        async def run():
            voice = SimpleNamespace(resolve=lambda: None)
            value = SimpleNamespace(id=2, resolve=lambda: None)
            interaction = SimpleNamespace(
                client=SimpleNamespace(fetch_channel=AsyncMock(return_value=voice))
            )
            return await RelayChannelTransformer().transform(interaction, value)

        with self.assertRaises(app_commands.TransformerError):
            asyncio.run(run())

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
