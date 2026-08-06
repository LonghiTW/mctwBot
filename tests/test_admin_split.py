"""Tests for permission boundaries after the admin cog split.

Covers:
- !relaylist: pure Discord guild permission (bot_admins irrelevant)
- !msg send: same-guild channel restriction
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.guild_admin.relaylist import Relaylist
from cogs.guild_admin.message_control import MessageControl


def _run(coro):
    return asyncio.run(coro)


class FakeMember(discord.Member):
    """Minimal Member subclass standing in for discord.Member instances."""

    __slots__ = ("_fake_guild", "_fake_perms")

    def __init__(self, manage_guild=False, administrator=False, view_audit_log=False):
        self._fake_guild = SimpleNamespace(id=123)
        self._fake_perms = SimpleNamespace(
            manage_guild=manage_guild,
            administrator=administrator,
            view_audit_log=view_audit_log,
        )

    @property
    def guild(self):
        return self._fake_guild

    @property
    def guild_permissions(self):
        return self._fake_perms


class RelaylistPermissionTests(unittest.TestCase):
    def setUp(self):
        self.cog = Relaylist(None)

    def test_discord_admin_can_view(self):
        self.assertTrue(self.cog.can_view_relaylist(FakeMember(manage_guild=True)))
        self.assertTrue(self.cog.can_view_relaylist(FakeMember(administrator=True)))

    def test_plain_member_cannot_view(self):
        self.assertFalse(self.cog.can_view_relaylist(FakeMember()))

    def test_bot_admin_without_discord_permission_cannot_view(self):
        # Even if the user is a bot admin, relaylist needs Discord guild permission.
        self.assertFalse(self.cog.can_view_relaylist(FakeMember()))

    def test_none_and_plain_objects_cannot_view(self):
        self.assertFalse(self.cog.can_view_relaylist(None))
        self.assertFalse(self.cog.can_view_relaylist(SimpleNamespace(id=1)))


class MessageControlPermissionTests(unittest.TestCase):
    def setUp(self):
        self.cog = MessageControl(None)
        self.msg_send = MessageControl.msg_send.callback

    def test_msg_send_rejects_channel_in_another_guild(self):
        ctx = SimpleNamespace(guild=SimpleNamespace(id=111), send=AsyncMock())
        channel = SimpleNamespace(guild=SimpleNamespace(id=222), id=999, name="other")

        _run(self.msg_send(self.cog, ctx, channel, payload='{"content":"hi"}'))

        ctx.send.assert_awaited_once()
        self.assertIn("只能傳送", ctx.send.await_args.args[0])

    def test_msg_send_rejects_when_no_guild(self):
        ctx = SimpleNamespace(guild=None, send=AsyncMock())
        channel = SimpleNamespace(guild=SimpleNamespace(id=222), id=999, name="other")

        _run(self.msg_send(self.cog, ctx, channel, payload='{"content":"hi"}'))

        ctx.send.assert_awaited_once()
        self.assertIn("只能傳送", ctx.send.await_args.args[0])

    def test_msg_send_same_guild_proceeds_with_audit(self):
        ctx = SimpleNamespace(guild=SimpleNamespace(id=111), author="tester", send=AsyncMock())
        channel = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            id=999,
            name="here",
            send=AsyncMock(return_value=SimpleNamespace(id=777)),
        )

        with patch("cogs.guild_admin.message_control.audit_admin_usage", new=AsyncMock()) as audit:
            _run(self.msg_send(self.cog, ctx, channel, payload='{"content":"hi"}'))

        audit.assert_awaited_once()
        channel.send.assert_awaited_once()
        self.assertIn("777", ctx.send.await_args.args[0])

    def test_invalid_json_raises_value_error(self):
        from utils.message_payload import message_from_json

        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            message_from_json("not json")
        with self.assertRaisesRegex(ValueError, "content or at least one embed"):
            message_from_json('{"embeds": []}')


if __name__ == "__main__":
    unittest.main()
