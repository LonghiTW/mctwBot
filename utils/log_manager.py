"""Structured logging utility."""
import logging
import sys


class LogManager:
    _logger = None

    @classmethod
    def _get_logger(cls) -> logging.Logger:
        if cls._logger is None:
            cls._logger = logging.getLogger("MCTW")
            cls._logger.setLevel(logging.DEBUG)
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            cls._logger.addHandler(handler)
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
