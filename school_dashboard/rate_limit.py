"""Failed-attempt limiter: in-process, thread-safe, bounded memory.

Keyed by whatever the caller passes (the Flask app passes `client_key(request.remote_addr)`). State is
per process, so with several workers each keeps its own count. Behind a reverse proxy remote_addr is
the proxy, so all clients share one bucket unless TRUSTED_PROXY_COUNT opts in to `trusted_client_ip`.
"""
import ipaddress
import logging
import math
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Optional


def client_key(addr: Optional[str]) -> str:
    """Normalise an address: IPv4-mapped IPv6 -> IPv4, other IPv6 -> its /64, junk -> 'unknown'."""
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return "unknown"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


MAX_TRUSTED_PROXIES = 10
_MAX_XFF_LEN = 1024
_log = logging.getLogger(__name__)
_warned_proxy_values: set = set()


def trusted_proxy_count(raw: Optional[str]) -> int:
    """Parse TRUSTED_PROXY_COUNT: an int in 0..MAX_TRUSTED_PROXIES; unset/empty -> 0.

    A non-empty invalid or out-of-range value also gives 0 (header ignored) and logs one WARNING per distinct value.
    """
    text = (raw or "").strip()
    if not text:
        return 0
    try:
        n = int(text)
    except ValueError:
        n = -1
    if 0 <= n <= MAX_TRUSTED_PROXIES:
        return n
    if text[:32] not in _warned_proxy_values:
        _warned_proxy_values.add(text[:32])
        _log.warning("TRUSTED_PROXY_COUNT is not an integer 0-%d; X-Forwarded-For is ignored", MAX_TRUSTED_PROXIES)
    return 0


def trusted_client_ip(remote_addr: Optional[str], forwarded_for: Optional[str], trusted: int) -> Optional[str]:
    """Client address given `trusted` proxy hops in front of us.

    With trusted == 0 (default) X-Forwarded-For is ignored. Otherwise the entry `trusted` places from the
    right is used (each trusted proxy appends the address it saw); entries to its left are client-controlled
    and never read. A header over 1024 characters is cut to its tail and its possibly truncated first entry
    dropped. Missing, too-short or unparseable headers fall back to remote_addr.
    """
    if trusted <= 0 or not forwarded_for:
        return remote_addr
    truncated = len(forwarded_for) > _MAX_XFF_LEN
    hops = [h.strip() for h in forwarded_for[-_MAX_XFF_LEN:].split(",")]
    if truncated:
        hops = hops[1:]
    if len(hops) < trusted:
        return remote_addr
    try:
        return str(ipaddress.ip_address(hops[-trusted]))
    except ValueError:
        return remote_addr


class FailureLimiter:
    def __init__(self, max_failures: int = 10, window: float = 60.0, max_keys: int = 1024,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_failures = max_failures
        self.window = window
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: "OrderedDict[str, deque]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._hits)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    def _live(self, key: str, now: float) -> Optional[deque]:
        dq = self._hits.get(key)
        if dq is None:
            return None
        while dq and now - dq[0] >= self.window:
            dq.popleft()
        if not dq:
            del self._hits[key]
            return None
        return dq

    def _wait(self, dq: Optional[deque], now: float) -> int:
        if dq is None or len(dq) < self.max_failures:
            return 0
        return max(1, math.ceil(dq[0] + self.window - now))

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may try again; 0 when not locked out."""
        with self._lock:
            now = self._clock()
            return self._wait(self._live(key, now), now)

    def fail(self, key: str) -> int:
        """Atomically: if `key` is locked out return the wait (nothing recorded), else record a failure, return 0."""
        with self._lock:
            now = self._clock()
            dq = self._live(key, now)
            wait = self._wait(dq, now)
            if wait:
                return wait
            if dq is None:
                dq = self._hits[key] = deque(maxlen=self.max_failures)
            dq.append(now)
            self._hits.move_to_end(key)
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)  # evict least recently failing client
            return 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
