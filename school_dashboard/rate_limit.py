"""Failed-attempt limiter: in-process, thread-safe, bounded memory.

Keyed by whatever the caller passes (the Flask app uses request.remote_addr). State is per
process, so with several workers each keeps its own count. Behind a reverse proxy remote_addr
is the proxy, so all clients share one bucket; no X-Forwarded-For is trusted here.
"""
import threading
import time
from collections import OrderedDict, deque


class FailureLimiter:
    def __init__(self, max_failures=10, window=60.0, max_keys=1024, clock=time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: "OrderedDict[str, deque]" = OrderedDict()

    def __len__(self):
        return len(self._hits)

    def clear(self):
        with self._lock:
            self._hits.clear()

    def _live(self, key, now):
        dq = self._hits.get(key)
        if dq is None:
            return None
        while dq and now - dq[0] >= self.window:
            dq.popleft()
        if not dq:
            del self._hits[key]
            return None
        return dq

    def retry_after(self, key) -> int:
        """Seconds until `key` may try again; 0 when not locked out."""
        with self._lock:
            now = self._clock()
            dq = self._live(key, now)
            if dq is None or len(dq) < self.max_failures:
                return 0
            return max(1, int(dq[0] + self.window - now) + 1)

    def record_failure(self, key) -> None:
        with self._lock:
            now = self._clock()
            dq = self._live(key, now)
            if dq is None:
                dq = self._hits[key] = deque(maxlen=self.max_failures)
            dq.append(now)
            self._hits.move_to_end(key)
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)  # evict least recently failing client

    def reset(self, key) -> None:
        with self._lock:
            self._hits.pop(key, None)
