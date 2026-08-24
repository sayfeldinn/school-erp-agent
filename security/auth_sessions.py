import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256


IDLE_TIMEOUT = timedelta(minutes=15)
ABSOLUTE_TIMEOUT = timedelta(hours=8)


class SessionService:
    def __init__(self, db_path, clock=None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._connection = sqlite3.connect(db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    def initialize(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                issued_role TEXT NOT NULL,
                issued_tenant_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        self._connection.commit()

    def create_session(self, user_id) -> str:
        try:
            user = self._connection.execute(
                "SELECT role, tenant_id FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if user is None:
                raise ValueError("session could not be created")

            token = secrets.token_urlsafe(32)
            token_hash = self._hash_token(token)
            created_at = self._now()
            expires_at = created_at + ABSOLUTE_TIMEOUT
            self._connection.execute(
                """
                INSERT INTO sessions (
                    token_hash, user_id, issued_role, issued_tenant_id,
                    created_at, last_seen_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    user_id,
                    user["role"],
                    user["tenant_id"],
                    created_at.isoformat(),
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            self._connection.commit()
        except sqlite3.Error:
            self._connection.rollback()
            raise ValueError("session could not be created") from None
        return token

    def resolve_session(self, token: str) -> dict | None:
        row = self._connection.execute(
            """
            SELECT
                sessions.id AS session_id,
                sessions.last_seen_at,
                sessions.expires_at,
                sessions.revoked_at,
                sessions.issued_role,
                sessions.issued_tenant_id,
                users.id AS user_id,
                users.email,
                users.role,
                users.tenant_id,
                users.status AS user_status,
                tenants.status AS tenant_status
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            JOIN tenants ON tenants.id = users.tenant_id
            WHERE sessions.token_hash = ?
            """,
            (self._hash_token(token),),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if row["user_status"] != "active" or row["tenant_status"] != "active":
            return None

        current_time = self._now()
        last_seen_at = datetime.fromisoformat(row["last_seen_at"])
        expires_at = datetime.fromisoformat(row["expires_at"])
        if current_time >= expires_at:
            return None
        if current_time - last_seen_at >= IDLE_TIMEOUT:
            return None

        if (
            row["role"] != row["issued_role"]
            or row["tenant_id"] != row["issued_tenant_id"]
        ):
            self._connection.execute(
                """
                UPDATE sessions
                SET revoked_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (current_time.isoformat(), row["session_id"]),
            )
            self._connection.commit()
            return None

        cursor = self._connection.execute(
            """
            UPDATE sessions
            SET last_seen_at = ?
            WHERE id = ? AND revoked_at IS NULL
            """,
            (current_time.isoformat(), row["session_id"]),
        )
        self._connection.commit()
        if cursor.rowcount != 1:
            return None

        return {
            "user_id": row["user_id"],
            "email": row["email"],
            "role": row["role"],
            "tenant_id": row["tenant_id"],
        }

    def revoke_session(self, token: str) -> None:
        self._connection.execute(
            """
            UPDATE sessions
            SET revoked_at = ?
            WHERE token_hash = ? AND revoked_at IS NULL
            """,
            (self._now().isoformat(), self._hash_token(token)),
        )
        self._connection.commit()

    def revoke_all_for_user(self, user_id) -> None:
        self._connection.execute(
            """
            UPDATE sessions
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (self._now().isoformat(), user_id),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def _now(self) -> datetime:
        current_time = self._clock()
        if current_time.tzinfo is None:
            raise ValueError("clock must return timezone-aware UTC")
        return current_time.astimezone(timezone.utc)

    @staticmethod
    def _hash_token(token: str) -> str:
        return sha256(token.encode("utf-8")).hexdigest()
