import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from utils.log_manager import LogManager


class LogRetentionTests(unittest.TestCase):
    def test_active_log_rotates_daily_and_old_archives_are_removed(self):
        now = datetime.now(timezone.utc)
        current_date = now.date()
        old_date = current_date - timedelta(days=1)
        with tempfile.TemporaryDirectory() as temp_dir, \
            patch("utils.log_manager.logging.getLogger", return_value=logging.Logger("isolated")), \
                patch.object(LogManager, "_logger", None), \
                patch.object(LogManager, "_log_dir", Path(temp_dir)), \
                patch.object(LogManager, "_file_handler", None), \
                patch.object(LogManager, "_active_log_date", old_date):
            logger = LogManager._get_logger()
            LogManager._active_log_date = old_date
            try:
                LogManager.info("TEST", "current log entry")

                expired = Path(temp_dir) / "bot-2020-01-01.log"
                expired.write_text("expired", encoding="utf-8")
                expired_time = now - timedelta(days=31)
                import os
                os.utime(expired, (expired_time.timestamp(), expired_time.timestamp()))

                removed = LogManager.prune_old_logs(int(now.timestamp()))

                self.assertEqual(removed, 1)
                self.assertFalse(expired.exists())
                self.assertTrue((Path(temp_dir) / f"bot-{old_date.isoformat()}.log").exists())
                self.assertTrue((Path(temp_dir) / "latest.log").exists())
                self.assertEqual(LogManager._active_log_date, current_date)
            finally:
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
