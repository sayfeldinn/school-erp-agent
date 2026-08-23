from datetime import datetime, timedelta, timezone

from security.auth_throttle import LoginThrottle


class FakeClock:
    def __init__(self):
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.current

    def advance(self, **delta):
        self.current += timedelta(**delta)


def test_email_is_blocked_after_five_failed_attempts():
    throttle = LoginThrottle(clock=FakeClock())
    email = "teacher.ahmed@school-a.edu"
    ip = "192.0.2.10"

    for _ in range(5):
        assert throttle.allow_attempt(email, ip) is True
        throttle.record_failure(email)

    assert throttle.allow_attempt(email, ip) is False


def test_email_bucket_is_case_insensitive():
    throttle = LoginThrottle(clock=FakeClock())
    variants = [
        "Teacher.Ahmed@School-A.edu",
        "teacher.ahmed@school-a.edu",
        "TEACHER.AHMED@SCHOOL-A.EDU",
        "Teacher.Ahmed@school-a.edu",
        "teacher.ahmed@School-A.edu",
    ]
    ip = "192.0.2.11"

    for email in variants:
        assert throttle.allow_attempt(email, ip) is True
        throttle.record_failure(email)

    assert throttle.allow_attempt("teacher.ahmed@school-a.edu", ip) is False


def test_different_email_has_independent_failure_bucket():
    throttle = LoginThrottle(clock=FakeClock())
    account_a = "account-a@school-a.edu"
    account_b = "account-b@school-a.edu"
    ip = "192.0.2.12"

    for _ in range(5):
        assert throttle.allow_attempt(account_a, ip) is True
        throttle.record_failure(account_a)

    assert throttle.allow_attempt(account_a, ip) is False
    assert throttle.allow_attempt(account_b, ip) is True


def test_ip_is_blocked_after_thirty_total_attempts():
    throttle = LoginThrottle(clock=FakeClock())
    ip = "192.0.2.13"

    for attempt in range(30):
        email = f"account-{attempt}@school-a.edu"
        assert throttle.allow_attempt(email, ip) is True

    assert throttle.allow_attempt("attempt-31@school-a.edu", ip) is False


def test_rate_limit_window_expires_after_fifteen_minutes():
    clock = FakeClock()
    throttle = LoginThrottle(clock=clock)
    email = "teacher.ahmed@school-a.edu"
    ip = "192.0.2.14"

    for _ in range(5):
        assert throttle.allow_attempt(email, ip) is True
        throttle.record_failure(email)

    assert throttle.allow_attempt(email, ip) is False

    clock.advance(minutes=15, seconds=1)

    assert throttle.allow_attempt(email, ip) is True


def test_success_clears_email_failures_but_not_ip_attempt_history():
    throttle = LoginThrottle(clock=FakeClock())
    email = "teacher.ahmed@school-a.edu"
    ip = "192.0.2.15"

    for _ in range(3):
        assert throttle.allow_attempt(email, ip) is True
        throttle.record_failure(email)

    throttle.record_success("Teacher.Ahmed@School-A.edu")

    assert throttle.allow_attempt(email, ip) is True

    for attempt in range(26):
        distinct_email = f"independent-{attempt}@school-a.edu"
        assert throttle.allow_attempt(distinct_email, ip) is True

    assert throttle.allow_attempt("attempt-31@school-a.edu", ip) is False
