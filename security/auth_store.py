import sqlite3
from datetime import datetime, timezone


class SecurityStore:
    def __init__(self, db_path):
        self._connection = sqlite3.connect(db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    def initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            );
            """
        )
        self._connection.commit()

    def create_tenant(self, tenant_id: str, name: str) -> None:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            self._connection.execute(
                "INSERT INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
                (tenant_id, name, created_at),
            )
            self._connection.commit()
        except sqlite3.IntegrityError:
            self._connection.rollback()
            raise ValueError("tenant could not be created") from None

    def get_tenant(self, tenant_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT id, name, status, created_at FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def create_user(
        self,
        email: str,
        password_hash: str,
        role: str,
        tenant_id: str,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            self._connection.execute(
                """
                INSERT INTO users (email, password_hash, role, tenant_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (email, password_hash, role, tenant_id, created_at),
            )
            self._connection.commit()
        except sqlite3.IntegrityError:
            self._connection.rollback()
            raise ValueError("user could not be created") from None

    def get_user_by_email(self, email: str) -> dict | None:
        row = self._connection.execute(
            """
            SELECT id, email, password_hash, role, tenant_id, status, created_at
            FROM users
            WHERE email = ? COLLATE NOCASE
            """,
            (email,),
        ).fetchone()
        return dict(row) if row is not None else None

    def close(self) -> None:
        self._connection.close()
