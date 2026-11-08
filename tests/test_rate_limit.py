"""FailureLimiter and client_key unit tests with an injected monotonic clock."""
import threading

from school_dashboard.rate_limit import FailureLimiter, client_key


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(**kw):
    clock = Clock()
    return FailureLimiter(max_failures=3, window=60.0, clock=clock, **kw), clock


def test_locks_at_max_failures_and_fail_reports_wait():
    lim, _ = make()
    assert [lim.fail("a") for _ in range(3)] == [0, 0, 0]
    assert lim.fail("a") == 60  # locked: not recorded, wait reported


def test_window_edge_is_inclusive_expiry():
    lim, clock = make()
    for _ in range(3):
        lim.fail("a")
    clock.t = 59.9
    assert lim.retry_after("a") == 1
    clock.t = 60.0  # exactly window old -> expired
    assert lim.retry_after("a") == 0


def test_retry_after_counts_down_from_oldest_failure():
    lim, clock = make()
    lim.fail("a")
    clock.t = 10
    lim.fail("a")
    lim.fail("a")
    clock.t = 20
    assert lim.retry_after("a") == 40  # oldest failure at t=0


def test_locked_attempts_do_not_extend_the_lockout():
    lim, clock = make()
    for _ in range(3):
        lim.fail("a")
    clock.t = 30
    for _ in range(50):
        lim.fail("a")
    clock.t = 60
    assert lim.retry_after("a") == 0


def test_reset_clears_one_client():
    lim, _ = make()
    for _ in range(3):
        lim.fail("a")
        lim.fail("b")
    lim.reset("a")
    assert lim.retry_after("a") == 0 and lim.retry_after("b") > 0


def test_lru_eviction_drops_least_recently_failing_first():
    clock = Clock()
    lim = FailureLimiter(max_failures=3, window=60.0, max_keys=2, clock=clock)
    lim.fail("a")
    lim.fail("b")
    lim.fail("a")  # a is now most recent
    lim.fail("c")  # evicts b
    assert len(lim) == 2
    assert set(lim._hits) == {"a", "c"}


def test_deque_never_holds_more_than_max_failures():
    lim, _ = make()
    for _ in range(3):
        lim.fail("a")
    assert len(lim._hits["a"]) == 3


def test_thread_safety_smoke():
    lim = FailureLimiter(max_failures=1000, window=60.0, max_keys=50)
    errors = []

    def work(n):
        try:
            for i in range(300):
                lim.fail(f"k{(n * 7 + i) % 80}")
                lim.retry_after(f"k{i % 80}")
                if i % 50 == 0:
                    lim.reset(f"k{i % 80}")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(lim) <= 50


def test_client_key_normalisation():
    assert client_key("192.0.2.7") == "192.0.2.7"
    assert client_key("::ffff:192.0.2.7") == "192.0.2.7"
    assert client_key("2001:db8:1:2::1") == client_key("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert client_key("2001:db8:1:3::1") != client_key("2001:db8:1:2::1")
    assert client_key(None) == "unknown"
    assert client_key("not an ip") == "unknown"
