import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.commands.privacy import Privacy


class PrivacyCommandTests(unittest.TestCase):
    def _run(self, url):
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        cog = Privacy(SimpleNamespace())
        with patch("cogs.commands.privacy.PRIVACY_POLICY_URL", url):
            asyncio.run(Privacy.privacy.callback(cog, interaction))
        return interaction

    def test_privacy_replies_with_configured_url(self):
        interaction = self._run("https://example.com/privacy")

        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("https://example.com/privacy", message)
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    def test_privacy_reports_missing_url(self):
        interaction = self._run("")

        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("尚未設定", message)


if __name__ == "__main__":
    unittest.main()
