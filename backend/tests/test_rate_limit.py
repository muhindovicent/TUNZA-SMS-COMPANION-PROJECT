from tunza.services.rate_limit import SlidingWindowRateLimiter


def test_allows_attempts_below_the_limit_and_reports_no_retry():
    limiter = SlidingWindowRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(2):
        limiter.record_failure("login:id:synthetic")
    assert limiter.retry_after("login:id:synthetic") is None
    assert limiter.retry_after("login:ip:127.0.0.1") is None


def test_blocks_at_the_limit_with_positive_retry_after():
    limiter = SlidingWindowRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.record_failure("login:id:synthetic")
    retry_after = limiter.retry_after("login:id:synthetic")
    assert retry_after is not None
    assert 1 <= retry_after <= 60


def test_reset_clears_recorded_failures():
    limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=60)
    limiter.record_failure("login:id:synthetic")
    limiter.record_failure("login:id:synthetic")
    assert limiter.retry_after("login:id:synthetic") is not None
    limiter.reset("login:id:synthetic")
    assert limiter.retry_after("login:id:synthetic") is None


def test_keys_are_tracked_independently():
    limiter = SlidingWindowRateLimiter(max_attempts=2, window_seconds=60)
    limiter.record_failure("login:id:first")
    limiter.record_failure("login:id:first")
    assert limiter.retry_after("login:id:first") is not None
    assert limiter.retry_after("login:id:second") is None
