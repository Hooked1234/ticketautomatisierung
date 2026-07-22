"""Versioned, local SQLite persistence for TicketPilot.

Only non-secret application data belongs here.  Credential material is refused
before SQL is executed and must be kept behind ``CredentialStore`` instead.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .security import assert_no_secret_fields, sanitize

Clock = Callable[[], datetime]
CURRENT_SCHEMA_VERSION = 3
DEFAULT_CACHE_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    up: tuple[str, ...]
    down: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetadataCacheEntry:
    key: str
    kind: str
    project_key: str | None
    value: Any
    fetched_at: datetime
    expires_at: datetime
    is_fresh: bool


@dataclass(frozen=True, slots=True)
class CacheStatus:
    total: int
    fresh: int
    stale: int
    oldest_fetch: datetime | None
    newest_fetch: datetime | None


_MIGRATIONS = (
    Migration(
        1,
        "initial local application storage",
        (
            """
            CREATE TABLE settings (
                key TEXT PRIMARY KEY NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE drafts (
                id TEXT PRIMARY KEY NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE tickets (
                local_id TEXT PRIMARY KEY NOT NULL,
                jira_key TEXT UNIQUE,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                entity_type TEXT,
                entity_id TEXT,
                details_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE sync_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_id TEXT,
                jira_key TEXT,
                action TEXT NOT NULL,
                outcome TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE metadata_cache (
                cache_key TEXT PRIMARY KEY NOT NULL,
                kind TEXT NOT NULL,
                project_key TEXT,
                value_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE comments (
                jira_key TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                author TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                PRIMARY KEY (jira_key, comment_id)
            )
            """,
        ),
        (
            "DROP TABLE IF EXISTS comments",
            "DROP TABLE IF EXISTS metadata_cache",
            "DROP TABLE IF EXISTS sync_results",
            "DROP TABLE IF EXISTS audit_events",
            "DROP TABLE IF EXISTS tickets",
            "DROP TABLE IF EXISTS drafts",
            "DROP TABLE IF EXISTS settings",
        ),
    ),
    Migration(
        2,
        "query indexes",
        (
            "CREATE INDEX idx_drafts_updated_at ON drafts(updated_at DESC)",
            "CREATE INDEX idx_tickets_updated_at ON tickets(updated_at DESC)",
            "CREATE INDEX idx_audit_occurred_at ON audit_events(occurred_at DESC)",
            "CREATE INDEX idx_sync_created_at ON sync_results(created_at DESC)",
            "CREATE INDEX idx_metadata_expiry ON metadata_cache(expires_at)",
            "CREATE INDEX idx_comments_updated ON comments(jira_key, updated_at DESC)",
        ),
        (
            "DROP INDEX IF EXISTS idx_comments_updated",
            "DROP INDEX IF EXISTS idx_metadata_expiry",
            "DROP INDEX IF EXISTS idx_sync_created_at",
            "DROP INDEX IF EXISTS idx_audit_occurred_at",
            "DROP INDEX IF EXISTS idx_tickets_updated_at",
            "DROP INDEX IF EXISTS idx_drafts_updated_at",
        ),
    ),
    Migration(
        3,
        "durable create attempt reservations",
        (
            """
            CREATE TABLE create_attempts (
                local_id TEXT PRIMARY KEY NOT NULL,
                request_fingerprint TEXT NOT NULL,
                message TEXT NOT NULL,
                reserved_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_create_attempts_reserved ON create_attempts(reserved_at DESC)",
        ),
        (
            "DROP INDEX IF EXISTS idx_create_attempts_reserved",
            "DROP TABLE IF EXISTS create_attempts",
        ),
    ),
)


class PersistenceError(RuntimeError):
    """Raised when local persistence cannot satisfy an operation."""


class SQLiteStore:
    """Thread-safe repository for local app state and the 24-hour cache."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        clock: Clock | None = None,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        auto_migrate: bool = True,
    ) -> None:
        if cache_ttl <= timedelta(0):
            raise ValueError("Cache TTL must be positive")
        self._clock = clock or (lambda: datetime.now(UTC))
        self.cache_ttl = cache_ttl
        self._lock = threading.RLock()
        self._closed = False
        self.path = str(path)
        if self.path != ":memory:":
            database_path = Path(self.path).expanduser()
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(database_path)
        self._connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._restrict_file_permissions()
        self._ensure_migration_table()
        if auto_migrate:
            self.migrate()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def set_cache_ttl(self, cache_ttl: timedelta) -> None:
        """Apply the TTL to cached and subsequently refreshed metadata.

        Persisted expiry timestamps are rebased from their original fetch
        time so cache status and the configured policy agree immediately.
        """

        if cache_ttl <= timedelta(0):
            raise ValueError("Cache TTL must be positive")
        with self._lock:
            self._ensure_open()
            with self._transaction():
                rows = self._connection.execute(
                    "SELECT cache_key, fetched_at FROM metadata_cache"
                ).fetchall()
                self._connection.executemany(
                    "UPDATE metadata_cache SET expires_at = ? WHERE cache_key = ?",
                    (
                        (
                            self._datetime_text(
                                self._parse_datetime(row["fetched_at"]) + cache_ttl
                            ),
                            row["cache_key"],
                        )
                        for row in rows
                    ),
                )
            self.cache_ttl = cache_ttl

    @property
    def schema_version(self) -> int:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            return int(row["version"])

    def migrate(self, target_version: int = CURRENT_SCHEMA_VERSION) -> None:
        """Move the schema up or down to a supported version transactionally."""

        if not 0 <= target_version <= CURRENT_SCHEMA_VERSION:
            raise PersistenceError(f"Unsupported schema target: {target_version}")
        with self._lock:
            self._ensure_open()
            current = self.schema_version
            if current > CURRENT_SCHEMA_VERSION:
                raise PersistenceError(
                    "Database schema is newer than this TicketPilot version supports"
                )
            if current < target_version:
                for migration in _MIGRATIONS:
                    if current < migration.version <= target_version:
                        self._apply_migration(migration, up=True)
            elif current > target_version:
                for migration in reversed(_MIGRATIONS):
                    if target_version < migration.version <= current:
                        self._apply_migration(migration, up=False)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def set_setting(self, key: str, value: Any) -> None:
        self.set_settings({key: value})

    def set_settings(self, values: Mapping[str, Any]) -> None:
        """Persist a related group of non-secret settings atomically."""

        now = self._now_text()
        rows: list[tuple[str, str, str]] = []
        for key, value in values.items():
            normalized = self._required_text(key, "setting key")
            assert_no_secret_fields({normalized: value})
            rows.append((normalized, self._encode(value), now))
        if not rows:
            return
        with self._transaction():
            self._connection.executemany(
                """
                INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        normalized = self._required_text(key, "setting key")
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT value_json FROM settings WHERE key = ?", (normalized,)
            ).fetchone()
        return default if row is None else self._decode(row["value_json"])

    def list_settings(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                "SELECT key, value_json FROM settings ORDER BY key"
            ).fetchall()
        return {row["key"]: self._decode(row["value_json"]) for row in rows}

    def delete_setting(self, key: str) -> bool:
        normalized = self._required_text(key, "setting key")
        with self._transaction():
            cursor = self._connection.execute("DELETE FROM settings WHERE key = ?", (normalized,))
        return cursor.rowcount > 0

    def save_draft(self, data: Mapping[str, Any], *, draft_id: str | None = None) -> str:
        assert_no_secret_fields(data)
        local_id = self._required_text(draft_id or str(uuid.uuid4()), "draft id")
        now = self._now_text()
        payload = self._encode(data)
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO drafts(id, data_json, created_at, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                (local_id, payload, now, now),
            )
        return local_id

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM drafts WHERE id = ?", (draft_id,))
        return None if row is None else self._draft_row(row)

    def list_drafts(self, *, limit: int = 500) -> list[dict[str, Any]]:
        checked_limit = self._limit(limit)
        rows = self._fetchall(
            "SELECT * FROM drafts ORDER BY updated_at DESC, id LIMIT ?", (checked_limit,)
        )
        return [self._draft_row(row) for row in rows]

    def delete_draft(self, draft_id: str) -> bool:
        with self._transaction():
            cursor = self._connection.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
        return cursor.rowcount > 0

    def upsert_ticket(
        self,
        local_id: str,
        data: Mapping[str, Any],
        *,
        jira_key: str | None = None,
        action: str = "CREATE",
        status: str = "DRAFT",
    ) -> None:
        assert_no_secret_fields(data)
        normalized_id = self._required_text(local_id, "local ticket id")
        normalized_key = jira_key.strip().upper() if jira_key else None
        now = self._now_text()
        payload = self._encode(data)
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO tickets(
                    local_id, jira_key, action, status, data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_id) DO UPDATE SET
                    jira_key = excluded.jira_key,
                    action = excluded.action,
                    status = excluded.status,
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_id,
                    normalized_key,
                    self._required_text(action, "action").upper(),
                    self._required_text(status, "status").upper(),
                    payload,
                    now,
                    now,
                ),
            )

    def get_ticket(self, identifier: str) -> dict[str, Any] | None:
        normalized = self._required_text(identifier, "ticket identifier")
        row = self._fetchone(
            "SELECT * FROM tickets WHERE local_id = ? OR jira_key = ? LIMIT 1",
            (normalized, normalized.upper()),
        )
        return None if row is None else self._ticket_row(row)

    def list_tickets(self, *, limit: int = 1_000) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM tickets ORDER BY updated_at DESC, local_id LIMIT ?",
            (self._limit(limit),),
        )
        return [self._ticket_row(row) for row in rows]

    def append_audit(
        self,
        event_type: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> int:
        now = self._now_text()
        payload = self._encode_redacted(details or {})
        with self._transaction():
            cursor = self._connection.execute(
                """
                INSERT INTO audit_events(
                    occurred_at, event_type, entity_type, entity_id, details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    now,
                    self._required_text(event_type, "event type"),
                    entity_type,
                    entity_id,
                    payload,
                ),
            )
        if cursor.lastrowid is None:  # pragma: no cover - SQLite INSERT invariant
            raise PersistenceError("Audit event ID was not generated")
        return int(cursor.lastrowid)

    def list_audit(self, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM audit_events ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (self._limit(limit),),
        )
        return [
            {
                "id": row["id"],
                "occurred_at": self._parse_datetime(row["occurred_at"]),
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "details": self._decode(row["details_json"]),
            }
            for row in rows
        ]

    def add_sync_result(
        self,
        *,
        action: str,
        outcome: str,
        message: str = "",
        local_id: str | None = None,
        jira_key: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> int:
        safe_message = str(sanitize(message, max_string=1_000))
        payload = self._encode_redacted(details or {})
        with self._transaction():
            cursor = self._connection.execute(
                """
                INSERT INTO sync_results(
                    local_id, jira_key, action, outcome, message, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    local_id,
                    jira_key,
                    self._required_text(action, "action").upper(),
                    self._required_text(outcome, "outcome").upper(),
                    safe_message,
                    payload,
                    self._now_text(),
                ),
            )
        if cursor.lastrowid is None:  # pragma: no cover - SQLite INSERT invariant
            raise PersistenceError("Sync result ID was not generated")
        return int(cursor.lastrowid)

    def reserve_create_attempt(
        self,
        *,
        local_id: str,
        request_fingerprint: str,
        message: str,
        reserved_at: datetime,
    ) -> bool:
        """Atomically persist the one-way guard before a CREATE POST.

        Reservations deliberately live outside ``sync_results``: a reservation
        is not a completed row outcome and must not create a second result or
        audit entry for an otherwise successful execution.
        """

        normalized_id = self._required_text(local_id, "local ticket id")
        timestamp = self._datetime_text(self._as_utc(reserved_at))
        safe_message = str(sanitize(message, max_string=1_000))
        with self._transaction():
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO create_attempts(
                    local_id, request_fingerprint, message, reserved_at
                ) VALUES (?, ?, ?, ?)
                """,
                (normalized_id, str(request_fingerprint), safe_message, timestamp),
            )
        return cursor.rowcount == 1

    def get_create_attempt(self, local_id: str) -> dict[str, Any] | None:
        normalized_id = self._required_text(local_id, "local ticket id")
        row = self._fetchone(
            "SELECT * FROM create_attempts WHERE local_id = ?",
            (normalized_id,),
        )
        if row is None:
            return None
        return {
            "local_id": row["local_id"],
            "request_fingerprint": row["request_fingerprint"],
            "message": row["message"],
            "reserved_at": self._parse_datetime(row["reserved_at"]),
        }

    def list_sync_results(
        self, *, local_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        params: tuple[Any, ...]
        if local_id is None:
            sql = "SELECT * FROM sync_results ORDER BY created_at DESC, id DESC LIMIT ?"
            params = (self._limit(limit),)
        else:
            sql = (
                "SELECT * FROM sync_results WHERE local_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?"
            )
            params = (local_id, self._limit(limit))
        rows = self._fetchall(sql, params)
        return [
            {
                "id": row["id"],
                "local_id": row["local_id"],
                "jira_key": row["jira_key"],
                "action": row["action"],
                "outcome": row["outcome"],
                "message": row["message"],
                "details": self._decode(row["details_json"]),
                "created_at": self._parse_datetime(row["created_at"]),
            }
            for row in rows
        ]

    def put_metadata(
        self,
        cache_key: str,
        kind: str,
        value: Any,
        *,
        project_key: str | None = None,
        ttl: timedelta | None = None,
        fetched_at: datetime | None = None,
    ) -> MetadataCacheEntry:
        assert_no_secret_fields(value)
        ttl_value = self.cache_ttl if ttl is None else ttl
        if ttl_value <= timedelta(0):
            raise ValueError("Metadata cache TTL must be positive")
        fetched = self._as_utc(fetched_at or self._clock())
        expires = fetched + ttl_value
        normalized_key = self._required_text(cache_key, "cache key")
        normalized_kind = self._required_text(kind, "metadata kind")
        project = project_key.strip().upper() if project_key else None
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO metadata_cache(
                    cache_key, kind, project_key, value_json, fetched_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    kind = excluded.kind,
                    project_key = excluded.project_key,
                    value_json = excluded.value_json,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                """,
                (
                    normalized_key,
                    normalized_kind,
                    project,
                    self._encode(value),
                    self._datetime_text(fetched),
                    self._datetime_text(expires),
                ),
            )
        return MetadataCacheEntry(
            key=normalized_key,
            kind=normalized_kind,
            project_key=project,
            value=value,
            fetched_at=fetched,
            expires_at=expires,
            is_fresh=expires > self._as_utc(self._clock()),
        )

    def get_metadata_entry(
        self, cache_key: str, *, allow_stale: bool = False
    ) -> MetadataCacheEntry | None:
        row = self._fetchone(
            "SELECT * FROM metadata_cache WHERE cache_key = ?", (cache_key,)
        )
        if row is None:
            return None
        now = self._as_utc(self._clock())
        expires = self._parse_datetime(row["expires_at"])
        fresh = expires > now
        if not fresh and not allow_stale:
            return None
        return MetadataCacheEntry(
            key=row["cache_key"],
            kind=row["kind"],
            project_key=row["project_key"],
            value=self._decode(row["value_json"]),
            fetched_at=self._parse_datetime(row["fetched_at"]),
            expires_at=expires,
            is_fresh=fresh,
        )

    def get_metadata(self, cache_key: str, *, allow_stale: bool = False) -> Any | None:
        entry = self.get_metadata_entry(cache_key, allow_stale=allow_stale)
        return None if entry is None else entry.value

    def cache_status(self) -> CacheStatus:
        now = self._now_text()
        row = self._fetchone(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN expires_at > ? THEN 1 ELSE 0 END), 0) AS fresh,
                MIN(fetched_at) AS oldest_fetch,
                MAX(fetched_at) AS newest_fetch
            FROM metadata_cache
            """,
            (now,),
        )
        if row is None:  # pragma: no cover - aggregate always returns one row
            return CacheStatus(0, 0, 0, None, None)
        total = int(row["total"])
        fresh = int(row["fresh"])
        return CacheStatus(
            total=total,
            fresh=fresh,
            stale=total - fresh,
            oldest_fetch=(
                self._parse_datetime(row["oldest_fetch"]) if row["oldest_fetch"] else None
            ),
            newest_fetch=(
                self._parse_datetime(row["newest_fetch"]) if row["newest_fetch"] else None
            ),
        )

    def purge_expired_metadata(self) -> int:
        with self._transaction():
            cursor = self._connection.execute(
                "DELETE FROM metadata_cache WHERE expires_at <= ?", (self._now_text(),)
            )
        return cursor.rowcount

    def delete_metadata(self, cache_key: str) -> bool:
        normalized = self._required_text(cache_key, "cache key")
        with self._transaction():
            cursor = self._connection.execute(
                "DELETE FROM metadata_cache WHERE cache_key = ?", (normalized,)
            )
        return cursor.rowcount > 0

    def clear_metadata(self) -> int:
        with self._transaction():
            cursor = self._connection.execute("DELETE FROM metadata_cache")
        return cursor.rowcount

    def upsert_comment(
        self,
        *,
        jira_key: str,
        comment_id: str,
        author: str,
        body: str,
        created_at: datetime | str,
        updated_at: datetime | str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        assert_no_secret_fields(data or {})
        key = self._required_text(jira_key, "Jira key").upper()
        identifier = self._required_text(comment_id, "comment id")
        created = self._datetime_value(created_at)
        updated = self._datetime_value(updated_at or created_at)
        seen = self._now_text()
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO comments(
                    jira_key, comment_id, author, body, created_at, updated_at,
                    first_seen_at, last_seen_at, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jira_key, comment_id) DO UPDATE SET
                    author = excluded.author,
                    body = excluded.body,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    last_seen_at = excluded.last_seen_at,
                    data_json = excluded.data_json
                """,
                (
                    key,
                    identifier,
                    self._required_text(author, "comment author"),
                    str(sanitize(body, max_depth=2, max_items=10_000, max_string=None)),
                    self._datetime_text(created),
                    self._datetime_text(updated),
                    seen,
                    seen,
                    self._encode(data or {}),
                ),
            )

    def list_comments(
        self,
        jira_key: str,
        *,
        since: datetime | str | None = None,
        limit: int = 1_000,
    ) -> list[dict[str, Any]]:
        key = self._required_text(jira_key, "Jira key").upper()
        if since is None:
            sql = (
                "SELECT * FROM comments WHERE jira_key = ? "
                "ORDER BY created_at DESC, comment_id LIMIT ?"
            )
            params: tuple[Any, ...] = (key, self._limit(limit))
        else:
            sql = (
                "SELECT * FROM comments WHERE jira_key = ? AND created_at > ? "
                "ORDER BY created_at DESC, comment_id LIMIT ?"
            )
            params = (
                key,
                self._datetime_text(self._datetime_value(since)),
                self._limit(limit),
            )
        return [self._comment_row(row) for row in self._fetchall(sql, params)]

    def _apply_migration(self, migration: Migration, *, up: bool) -> None:
        statements = migration.up if up else migration.down
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for statement in statements:
                self._connection.execute(statement)
            if up:
                self._connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, self._now_text()),
                )
            else:
                self._connection.execute(
                    "DELETE FROM schema_migrations WHERE version = ?", (migration.version,)
                )
            user_version = migration.version if up else migration.version - 1
            self._connection.execute(f"PRAGMA user_version = {user_version}")
            self._connection.execute("COMMIT")
        except sqlite3.Error as error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise PersistenceError(
                f"Schema migration {migration.version} ({'up' if up else 'down'}) failed"
            ) from error

    def _ensure_migration_table(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def _fetchone(self, sql: str, params: Sequence[Any]) -> sqlite3.Row | None:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(sql, tuple(params)).fetchone()
            return row if isinstance(row, sqlite3.Row) else None

    def _fetchall(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        with self._lock:
            self._ensure_open()
            return self._connection.execute(sql, tuple(params)).fetchall()

    def _draft_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "data": self._decode(row["data_json"]),
            "created_at": self._parse_datetime(row["created_at"]),
            "updated_at": self._parse_datetime(row["updated_at"]),
        }

    def _ticket_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "local_id": row["local_id"],
            "jira_key": row["jira_key"],
            "action": row["action"],
            "status": row["status"],
            "data": self._decode(row["data_json"]),
            "created_at": self._parse_datetime(row["created_at"]),
            "updated_at": self._parse_datetime(row["updated_at"]),
        }

    def _comment_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "jira_key": row["jira_key"],
            "comment_id": row["comment_id"],
            "author": row["author"],
            "body": row["body"],
            "created_at": self._parse_datetime(row["created_at"]),
            "updated_at": self._parse_datetime(row["updated_at"]),
            "first_seen_at": self._parse_datetime(row["first_seen_at"]),
            "last_seen_at": self._parse_datetime(row["last_seen_at"]),
            "data": self._decode(row["data_json"]),
        }

    @staticmethod
    def _encode(value: Any) -> str:
        assert_no_secret_fields(value)
        safe_value = sanitize(value, max_depth=64, max_items=100_000, max_string=None)
        return json.dumps(
            safe_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _encode_redacted(value: Any) -> str:
        safe_value = sanitize(value, max_depth=16, max_items=1_000, max_string=2_000)
        return json.dumps(
            safe_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _decode(value: str) -> Any:
        return json.loads(value)

    def _now_text(self) -> str:
        return self._datetime_text(self._as_utc(self._clock()))

    @staticmethod
    def _datetime_text(value: datetime) -> str:
        return SQLiteStore._as_utc(value).isoformat(timespec="microseconds")

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        return SQLiteStore._as_utc(datetime.fromisoformat(value))

    @staticmethod
    def _datetime_value(value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return SQLiteStore._as_utc(value)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return SQLiteStore._as_utc(parsed)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _required_text(value: object, name: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{name} must not be empty")
        return text

    @staticmethod
    def _limit(value: int) -> int:
        if not 1 <= value <= 10_000:
            raise ValueError("Limit must be between 1 and 10000")
        return value

    def _restrict_file_permissions(self) -> None:
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _ensure_open(self) -> None:
        if self._closed:
            raise PersistenceError("SQLite store is closed")


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_CACHE_TTL",
    "CacheStatus",
    "MetadataCacheEntry",
    "PersistenceError",
    "SQLiteStore",
]
