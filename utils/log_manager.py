"""Structured logging utility — console + latest.log with shutdown archiving."""
import logging
import sys
from pathlib import Path
from datetime import date


class LogManager:
    _logger = None
    _log_dir = Path.cwd() / "log"
    _file_handler = None

    @classmethod
    def _ensure_log_dir(cls):
        cls._log_dir.mkdir(exist_ok=True)

    @classmethod
    def _archive_latest(cls):
        """Move existing latest.log to bot-YYYY-MM-DD.log on startup."""
        latest = cls._log_dir / "latest.log"
        if not latest.exists():
            return
        archive = cls._log_dir / f"bot-{date.today().isoformat()}.log"
        if archive.exists():
            counter = 1
            while archive.exists():
                archive = cls._log_dir / f"bot-{date.today().isoformat()}_{counter}.log"
                counter += 1
        latest.rename(archive)

    @classmethod
    def _get_logger(cls) -> logging.Logger:
        if cls._logger is None:
            cls._ensure_log_dir()
            cls._archive_latest()

            cls._logger = logging.getLogger("MCTW")
            cls._logger.setLevel(logging.DEBUG)

            fmt = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            # Console handler
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(fmt)
            cls._logger.addHandler(console)

            # File handler — always writes to latest.log
            log_file = cls._log_dir / "latest.log"
            cls._file_handler = logging.FileHandler(log_file, encoding="utf-8")
            cls._file_handler.setFormatter(fmt)
            cls._logger.addHandler(cls._file_handler)

        return cls._logger

    @classmethod
    def shutdown(cls):
        """Archive latest.log by date on graceful shutdown."""
        if cls._file_handler:
            cls._file_handler.close()
            if cls._logger:
                cls._logger.removeHandler(cls._file_handler)
            cls._file_handler = None
        cls._archive_latest()

    @classmethod
    def info(cls, tag: str, msg: str, exec_id: str | None = None):
        cls._get_logger().info(f"[{tag}] {cls._fmt(msg, exec_id)}")

    @classmethod
    def warn(cls, tag: str, msg: str, exec_id: str | None = None):
        cls._get_logger().warning(f"[{tag}] {cls._fmt(msg, exec_id)}")

    @classmethod
    def error(cls, tag: str, msg: str, exec_id: str | None = None, exc_info=None):
        cls._get_logger().error(f"[{tag}] {cls._fmt(msg, exec_id)}", exc_info=exc_info)

    @classmethod
    def debug(cls, tag: str, msg: str, exec_id: str | None = None):
        cls._get_logger().debug(f"[{tag}] {cls._fmt(msg, exec_id)}")

    @staticmethod
    def _fmt(msg: str, exec_id: str | None) -> str:
        return f"[{exec_id}] {msg}" if exec_id else msg
