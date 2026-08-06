"""Tests for the relay_reverse_delete bot admin bypass in MessageSync."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

from cogs.relay.message_sync import MessageSync


def _run(coro):
    return asyncio.run(coro)


class FakeMember(discord.Member):
    """Minimal Member subclass standing in for discord.Member instances."""

    __slots__ = ("_fake_guild", "_fake_perms")

    def __init__(self, view_audit_log=False):
        self._fake_guild = SimpleNamespace(id=123)
        self._fake_perms = SimpleNamespace(
            manage_guild=False,
            administrator=False,
            view_audit_log=view_audit_log,
        )

    @property
    def guild(self):
        return self._fake_guild

    @property
    def guild_permissions(self):
        return self._fake_perms


class ReverseDeleteBypassTests(unittest.TestCase):
    def test_denied_when_no_feature_admins_configured(self):
        guild = SimpleNamespace(me=FakeMember(view_audit_log=True))
        sync = MessageSync(None)

        with patch("cogs.relay.message_sync.bot_admin_ids_with_feature", return_value=set()):
            self.assertFalse(_run(sync._bot_admin_reverse_delete(guild, "123")))

    def test_denied_without_view_audit_log_permission(self):
        guild = SimpleNamespace(me=FakeMember())
        sync = MessageSync(None)

        with patch("cogs.relay.message_sync.bot_admin_ids_with_feature", return_value={111}):
            self.assertFalse(_run(sync._bot_admin_reverse_delete(guild, "123")))

    def test_granted_when_bot_admin_deleted(self):
        async def audit_logs(action, limit):
            yield SimpleNamespace(
                target=SimpleNamespace(id=123),
                user=SimpleNamespace(id=111),
            )

        guild = SimpleNamespace(
            me=FakeMember(view_audit_log=True),
            audit_logs=audit_logs,
        )
        sync = MessageSync(None)

        with patch("cogs.relay.message_sync.bot_admin_ids_with_feature", return_value={111}), \
             patch("cogs.relay.message_sync.bot_admin_has_feature", return_value=True):
            self.assertTrue(_run(sync._bot_admin_reverse_delete(guild, "123")))

    def test_denied_when_non_admin_deleted(self):
        async def audit_logs(action, limit):
            yield SimpleNamespace(
                target=SimpleNamespace(id=123),
                user=SimpleNamespace(id=999),
            )

        guild = SimpleNamespace(
            me=FakeMember(view_audit_log=True),
            audit_logs=audit_logs,
        )
        sync = MessageSync(None)

        with patch("cogs.relay.message_sync.bot_admin_ids_with_feature", return_value={111}), \
             patch("cogs.relay.message_sync.bot_admin_has_feature", return_value=False):
            self.assertFalse(_run(sync._bot_admin_reverse_delete(guild, "123")))

    def test_denied_when_audit_entry_not_found(self):
        async def audit_logs(action, limit):
            yield SimpleNamespace(
                target=SimpleNamespace(id=99999),
                user=SimpleNamespace(id=111),
            )

        guild = SimpleNamespace(
            me=FakeMember(view_audit_log=True),
            audit_logs=audit_logs,
        )
        sync = MessageSync(None)

        with patch("cogs.relay.message_sync.bot_admin_ids_with_feature", return_value={111}), \
             patch("cogs.relay.message_sync.bot_admin_has_feature", return_value=True):
            self.assertFalse(_run(sync._bot_admin_reverse_delete(guild, "123")))

    def test_denied_on_forbidden(self):
        async def audit_logs(action, limit):
            raise discord.Forbidden("no")
            yield  # pragma: no cover - makes this an async generator

        guild = SimpleNamespace(
            me=FakeMember(view_audit_log=True),
            audit_logs=audit_logs,
        )
        sync = MessageSync(None)

        with patch("cogs.relay.message_sync.bot_admin_ids_with_feature", return_value={111}):
            self.assertFalse(_run(sync._bot_admin_reverse_delete(guild, "123")))


if __name__ == "__main__":
    unittest.main()
