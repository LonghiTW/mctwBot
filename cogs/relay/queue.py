"""Async webhook relay queue — parallel workers to avoid head-of-line blocking."""
import asyncio

import aiohttp

from database import DatabaseManager
from utils.log_manager import LogManager
from app.config import RELAY_QUEUE_DELAY_MS, RELAY_QUEUE_WORKERS

log = LogManager


class RelayQueue:
    """Queue with parallel workers — per-webhook delay avoids 429s."""

    def __init__(self, delay_ms: int = 600, max_retries: int = 3, workers: int = 4):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._delay = delay_ms / 1000
        self._max_retries = max_retries
        self._worker_count = workers
        self._tasks: list[asyncio.Task] = []
        self._session: aiohttp.ClientSession | None = None
        self._cancelled: set[str] = set()
        self._last_send: dict[str, float] = {}  # webhook_url -> last send timestamp

    def cancel(self, original_msg_id: str) -> None:
        """Mark an original message as cancelled (deleted) so queued sends are skipped."""
        self._cancelled.add(original_msg_id)

    async def start(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        self._tasks = [
            asyncio.create_task(self._processor(f"worker-{i}"))
            for i in range(self._worker_count)
        ]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        self._tasks = []
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

    async def _processor(self, worker_name: str = ""):
        while True:
            item = await self._queue.get()
            original_msg_id = item.get("meta", {}).get("original_msg_id", "")
            if original_msg_id and original_msg_id in self._cancelled:
                self._cancelled.discard(original_msg_id)
                log.info("QUEUE-SKIP", f"Original {original_msg_id} deleted, skipping queued send")
                continue
            try:
                wh_url: str = item["webhook_url"]
                now = asyncio.get_running_loop().time()
                since_last = now - self._last_send.get(wh_url, 0)
                if since_last < self._delay:
                    await asyncio.sleep(self._delay - since_last)
                await self._send(item)
                self._last_send[wh_url] = asyncio.get_running_loop().time()
            except Exception as exc:
                log.error("QUEUE", f"Critical: {exc}", exc_info=exc)

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
                # thread_name goes in JSON body for forum channel webhooks
                payload = {**payload, "thread_name": meta["thread_name"]}
                log.info("QUEUE", f"Posting with thread_name={meta['thread_name']!r}", exec_id)

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
                    if resp.status == 404 and "10003" in body and meta.get("target_thread_id"):
                        db = DatabaseManager()
                        db.execute(
                            "DELETE FROM relay_threads WHERE target_thread_id = ?",
                            (str(meta["target_thread_id"]),),
                        )
                        db.commit()
                        log.warn("THREAD-MAP", f"Removed stale thread mapping {meta['target_thread_id']}", exec_id)
                    return

                data = await resp.json()

                # For forum webhooks, the response message's channel_id is the created post/thread.
                response_channel_id = data.get("channel_id")
                created_thread_id = response_channel_id if meta.get("thread_name") else None
                target_channel_id = str(created_thread_id or response_channel_id or meta.get("target_channel_id"))
                if meta.get("thread_name") and created_thread_id:
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
                        log.info(
                            "THREAD-MAP",
                            f"Saved {meta.get('source_thread_id')} -> {target_channel_id}",
                            exec_id,
                        )
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

                # Post-send cancel check: if original was marked deleted while
                # this webhook was in-flight, immediately delete what we just sent.
                if relayed_id and meta.get("original_msg_id", "") in self._cancelled:
                    log.info(
                        "QUEUE-CANCEL",
                        f"Original {meta['original_msg_id']} deleted during send, deleting {relayed_id}",
                        exec_id,
                    )
                    try:
                        delete_url = f"{wh_url}/messages/{relayed_id}"
                        delete_params: dict[str, str] = {}
                        if meta.get("target_thread_id"):
                            delete_params["thread_id"] = meta["target_thread_id"]
                        async with self._session.delete(
                            delete_url, params=delete_params, raise_for_status=False
                        ) as del_resp:
                            if del_resp.status not in (204, 404):
                                log.warn(
                                    "QUEUE-CANCEL",
                                    f"Delete returned {del_resp.status}",
                                    exec_id,
                                )
                    except Exception as e:
                        log.warn("QUEUE-CANCEL", f"Failed to delete {relayed_id}: {e}", exec_id)

        except (aiohttp.ClientError, asyncio.TimeoutError) as net_err:
            if attempt < self._max_retries:
                log.warn("QUEUE-RETRY", f"Attempt {attempt+1}/{self._max_retries}: {net_err}", exec_id)
                item["attempt"] += 1
                await asyncio.sleep(2 ** attempt)
                await self._queue.put(item)


# Module-level singleton
relay_queue = RelayQueue(delay_ms=RELAY_QUEUE_DELAY_MS, workers=RELAY_QUEUE_WORKERS)
