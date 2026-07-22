"""Structured logging utility — console + file output."""
import logging
import sys
from pathlib import Path


class LogManager:
    _logger = None
    _log_dir = Path.cwd() / "log"

    @classmethod
    def _ensure_log_dir(cls):
        cls._log_dir.mkdir(exist_ok=True)

    @classmethod
    def _get_logger(cls) -> logging.Logger:
        if cls._logger is None:
            cls._ensure_log_dir()
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

            # File handler — daily rotating by date in filename
            from datetime import date
            log_file = cls._log_dir / f"bot-{date.today().isoformat()}.log"
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            cls._logger.addHandler(fh)

        return cls._logger

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
