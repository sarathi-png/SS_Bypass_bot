import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: Optional[float] = None


class RateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def _key(self, chat_id: int, user_id: int) -> str:
        return f"{chat_id}:{user_id}"

    def _prune(self, key: str):
        now = time.monotonic()
        bucket = self._buckets[key]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def check(self, chat_id: int, user_id: int) -> RateLimitResult:
        key = self._key(chat_id, user_id)
        self._prune(key)
        bucket = self._buckets[key]

        if len(bucket) >= self.max_requests:
            oldest = bucket[0]
            retry_after = max(0.0, oldest + self.window_seconds - time.monotonic())
            return RateLimitResult(allowed=False, remaining=0, retry_after=retry_after)

        return RateLimitResult(allowed=True, remaining=self.max_requests - len(bucket))

    def consume(self, chat_id: int, user_id: int) -> RateLimitResult:
        key = self._key(chat_id, user_id)
        self._prune(key)
        bucket = self._buckets[key]

        if len(bucket) >= self.max_requests:
            oldest = bucket[0]
            retry_after = max(0.0, oldest + self.window_seconds - time.monotonic())
            return RateLimitResult(allowed=False, remaining=0, retry_after=retry_after)

        bucket.append(time.monotonic())
        return RateLimitResult(allowed=True, remaining=self.max_requests - len(bucket))

    def get_stats(self) -> dict:
        now = time.monotonic()
        active = 0
        for key, bucket in list(self._buckets.items()):
            cutoff = now - self.window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if bucket:
                active += 1
            else:
                del self._buckets[key]
        return {"active_users": active, "max_requests": self.max_requests, "window_seconds": self.window_seconds}
