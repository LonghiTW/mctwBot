"""Async webhook relay queue — serialises sends to avoid 429 rate limits."""
import asyncio

import aiohttp

from database import DatabaseManager
from utils.log_manager import LogManager

log = LogManager


class RelayQueue:
    """FIFO queue that sends webhook payloads one-at-a-time with a delay."""

    def __init__(self, delay_ms: int = 600, max_retries: int = 3):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._delay = delay_ms / 1000
        self._max_retries = max_retries
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._processor())

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        if self._session:
            await self._session.close()
            self._session = None

    async def add(self, webhook_url: str, payload: dict, meta: dict):
        await self._queue.put({
            "webhook_url": webhook_url,
            "payload": payload,
            "meta": meta,
            "attempt": 1,
        })

    async def _processor(self):
        while True:
            item = await self._queue.get()
            try:
                await self._send(item)
            except Exception as exc:
                log.error("QUEUE", f"Critical: {exc}", exc_info=exc)
            await asyncio.sleep(self._delay)

    async def _send(self, item: dict):
        wh_url: str = item["webhook_url"]
        payload: dict = item["payload"]
        meta: dict = item["meta"]
        attempt: int = item["attempt"]
        exec_id: str = meta.get("execution_id", "")

        try:
            params = {"wait": "true"}
            if meta.get("target_thread_id"):
                params["thread_id"] = meta["target_thread_id"]
            elif meta.get("thread_name"):
                params["thread_name"] = meta["thread_name"]

            async with self._session.post(
                wh_url, json=payload, params=params, raise_for_status=False
            ) as resp:
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", 5))
                    log.warn("QUEUE-429", f"Retry after {retry_after}s", exec_id)
                    await asyncio.sleep(retry_after)
                    await self._queue.put(item)
                    return

                if resp.status == 204:
                    log.info("QUEUE-SEND", f"Delivered (204) to {meta.get('target_channel_id')} — no tracking data", exec_id)
                    return

                if resp.status >= 400:
                    body = await resp.text()
                    log.error("QUEUE-FAIL", f"HTTP {resp.status}: {body[:200]}", exec_id)
                    return

                data = await resp.json()

                # Save thread mapping if a thread was created
                target_channel_id = str(data.get("channel_id") or meta.get("target_channel_id"))
                if meta.get("thread_name") and data.get("channel_id"):
                    db = DatabaseManager()
                    try:
                        db.execute(
                            """INSERT OR REPLACE INTO relay_threads
                               (group_id, source_thread_id, source_parent_channel_id,
                                target_parent_channel_id, target_thread_id)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                meta.get("group_id"),
                                meta.get("source_thread_id"),
                                meta.get("source_parent_channel_id"),
                                meta.get("target_parent_channel_id"),
                                target_channel_id,
                            ),
                        )
                        db.commit()
                    except Exception as e:
                        log.error("QUEUE-DB", f"Save thread mapping failed: {e}", exec_id)

                # Save relay mapping
                relayed_id = data.get("id")
                if relayed_id:
                    db = DatabaseManager()
                    try:
                        db.execute(
                            """INSERT INTO relayed_messages
                               (original_message_id, original_channel_id,
                                relayed_message_id, relayed_channel_id, replied_to_id)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                meta.get("original_msg_id"),
                                meta.get("original_channel_id"),
                                relayed_id,
                                target_channel_id,
                                meta.get("replied_to_id"),
                            ),
                        )
                        db.commit()
                    except Exception as e:
                        log.error("QUEUE-DB", f"Save relay record failed: {e}", exec_id)

                log.info("QUEUE-SEND", f"Delivered to {target_channel_id} (msg {relayed_id})", exec_id)

        except (aiohttp.ClientError, asyncio.TimeoutError) as net_err:
            if attempt < self._max_retries:
                log.warn("QUEUE-RETRY", f"Attempt {attempt+1}/{self._max_retries}: {net_err}", exec_id)
                item["attempt"] += 1
                await asyncio.sleep(2 ** attempt)
                await self._queue.put(item)


# Module-level singleton
relay_queue = RelayQueue()
