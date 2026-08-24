import secrets
import sqlite3

from security.auth_passwords import hash_password, verify_password
from security.auth_sessions import SessionService
from security.auth_store import SecurityStore


_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


class LoginService:
    def __init__(self, db_path, clock=None):
        self._db_path = db_path
        self._clock = clock

    def authenticate(self, email: str, password: str) -> str | None:
        try:
            store = SecurityStore(self._db_path)
            try:
                user = store.get_user_by_email(email)
                if user is None:
                    verify_password(password, _DUMMY_PASSWORD_HASH)
                    return None

                if not verify_password(password, user["password_hash"]):
                    return None
                if user["status"] != "active":
                    return None

                tenant = store.get_tenant(user["tenant_id"])
                if tenant is None or tenant["status"] != "active":
                    return None
            finally:
                store.close()

            sessions = SessionService(self._db_path, clock=self._clock)
            try:
                sessions.initialize()
                return sessions.create_session(user["id"])
            finally:
                sessions.close()
        except sqlite3.Error:
            return None
