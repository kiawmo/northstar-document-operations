"""Redis wakes idle workers and exposes short-lived heartbeats; SQL owns every job.

Hints contain only job IDs. Losing a hint or all Redis storage never loses a
source, lease, review, invoice, or acknowledgment. Workers still poll SQL.
"""

import logging
from uuid import uuid4

from .config import Settings


logger = logging.getLogger("docops.signals")


class QueueSignals:
    def __init__(self, settings: Settings):
        self.prefix = settings.redis_prefix
        self.worker_id = uuid4().hex
        self.client = None
        if settings.redis_url:
            import redis
            from redis.backoff import NoBackoff
            from redis.retry import Retry

            self.client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.3,
                socket_timeout=1,
                retry_on_timeout=False,
                retry=Retry(NoBackoff(), 0),
                max_connections=8,
            )
        self._last_failure = None

    def _unavailable(self, exc: Exception) -> None:
        # Redis exceptions may include credentials/URLs; only type is safe.
        if type(exc).__name__ != self._last_failure:
            logger.warning("Redis wakeup unavailable; SQL polling retained (%s)", type(exc).__name__)
            self._last_failure = type(exc).__name__

    def notify(self, job_id: str) -> bool:
        if self.client is None:
            return False
        try:
            with self.client.pipeline(transaction=True) as pipe:
                pipe.lpush(self.prefix + ":wakeups", job_id)
                pipe.ltrim(self.prefix + ":wakeups", 0, 999)
                pipe.incr(self.prefix + ":notifications")
                pipe.execute()
            self._last_failure = None
            return True
        except Exception as exc:
            self._unavailable(exc)
            return False

    def wait(self, timeout: float = 0.5) -> bool:
        if self.client is None:
            return False
        try:
            result = self.client.brpop(self.prefix + ":wakeups", timeout=timeout)
            self._last_failure = None
            return result is not None
        except Exception as exc:
            self._unavailable(exc)
            return False

    def heartbeat(self) -> None:
        if self.client is None:
            return
        try:
            self.client.set(self.prefix + ":worker:" + self.worker_id, "alive", ex=30)
        except Exception as exc:
            self._unavailable(exc)

    def status(self) -> dict:
        if self.client is None:
            return {"configured": False, "status": "disabled", "sql_polling": True}
        try:
            self.client.ping()
            return {
                "configured": True,
                "status": "ready",
                "sql_polling": True,
                "pending_wakeups": self.client.llen(self.prefix + ":wakeups"),
                "notifications": int(self.client.get(self.prefix + ":notifications") or 0),
                "active_workers": sum(1 for _ in self.client.scan_iter(self.prefix + ":worker:*")),
            }
        except Exception as exc:
            self._unavailable(exc)
            return {"configured": True, "status": "unavailable", "sql_polling": True}
