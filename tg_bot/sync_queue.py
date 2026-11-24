from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import requests
from telebot.apihelper import ApiTelegramException

logger = logging.getLogger("cardinal.telegram")

FLOOD_RETRIES = 5
TRANSIENT_ATTEMPTS = 3
TRANSIENT_PAUSE = 2
THREAD_MISSING = ("thread not found", "topic_deleted", "topic_id_invalid")
LOST_GROUP = ("chat not found", "not enough rights", "chat_admin_required", "have no rights")


@dataclass
class Job:
    acc_id: str
    chat_id: str
    text: str
    keyboard: object = None
    photo: str | bytes | None = None
    fallback: tuple | None = None
    source_message_id: str | None = None


def classify(e: Exception) -> str:
    if isinstance(e, ApiTelegramException):
        code = e.error_code
        text = (e.description or "").lower()
        if code == 429:
            return "flood"
        if code == 400 and any(s in text for s in THREAD_MISSING):
            return "thread_missing"
        if code == 403 or (code == 400 and any(s in text for s in LOST_GROUP)):
            return "lost_group"
        if code >= 500:
            return "transient"
        return "other"
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return "transient"
    return "other"


def retry_after(e: Exception) -> float:
    params = (getattr(e, "result_json", None) or {}).get("parameters") or {}
    return float(params.get("retry_after", 5))


class GroupQueue:
    def __init__(self, deliver: Callable[[Job], None], on_failed: Callable[[Job, Exception], None],
                 sleep: Callable[[float], None] = time.sleep):
        self.deliver = deliver
        self.on_failed = on_failed
        self.sleep = sleep
        self._jobs: deque[Job] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def put(self, job: Job) -> None:
        with self._lock:
            self._jobs.append(job)
        self._wake.set()

    def _next(self) -> Job | None:
        with self._lock:
            return self._jobs.popleft() if self._jobs else None

    def drain(self) -> int:
        count = 0
        while (job := self._next()) is not None:
            self._run(job)
            count += 1
        return count

    def _run(self, job: Job) -> None:
        floods = transient = 0
        while True:
            try:
                self.deliver(job)
                return
            except Exception as e:
                kind = classify(e)
                if kind == "flood" and floods < FLOOD_RETRIES:
                    floods += 1
                    self.sleep(retry_after(e))
                    continue
                if kind == "transient" and transient < TRANSIENT_ATTEMPTS - 1:
                    transient += 1
                    self.sleep(TRANSIENT_PAUSE)
                    continue
                try:
                    self.on_failed(job, e)
                except Exception:
                    logger.exception("Ошибка обработки неотправленного сообщения в группу")
                return

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="chat-sync")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            try:
                self.drain()
            except Exception:
                logger.exception("Ошибка очереди синхронизации чатов")
