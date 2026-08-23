from collections import deque
from datetime import datetime, timedelta, timezone


WINDOW = timedelta(minutes=15)
EMAIL_FAILURE_LIMIT = 5
IP_ATTEMPT_LIMIT = 30


class LoginThrottle:
    def __init__(self, clock=None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._email_failures = {}
        self._ip_attempts = {}

    def allow_attempt(self, email: str, ip: str) -> bool:
        current_time = self._now()
        normalized_email = email.casefold()
        email_failures = self._prune(
            self._email_failures, normalized_email, current_time
        )
        ip_attempts = self._prune(self._ip_attempts, ip, current_time)

        if len(email_failures) >= EMAIL_FAILURE_LIMIT:
            return False
        if len(ip_attempts) >= IP_ATTEMPT_LIMIT:
            return False

        if ip not in self._ip_attempts:
            self._ip_attempts[ip] = ip_attempts
        ip_attempts.append(current_time)
        return True

    def record_failure(self, email: str) -> None:
        current_time = self._now()
        normalized_email = email.casefold()
        failures = self._prune(
            self._email_failures, normalized_email, current_time
        )
        if normalized_email not in self._email_failures:
            self._email_failures[normalized_email] = failures
        failures.append(current_time)

    def record_success(self, email: str) -> None:
        self._email_failures.pop(email.casefold(), None)

    def _now(self) -> datetime:
        current_time = self._clock()
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("clock must return timezone-aware UTC")
        return current_time.astimezone(timezone.utc)

    @staticmethod
    def _prune(buckets, key, current_time):
        events = buckets.get(key, deque())
        while events and current_time - events[0] >= WINDOW:
            events.popleft()
        if not events:
            buckets.pop(key, None)
        return events
