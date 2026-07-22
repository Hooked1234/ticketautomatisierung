"""Dependency-inversion ports used by TicketPilot application services."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any, Protocol, runtime_checkable

from ticketpilot.domain.models import (
    Attachment,
    Comment,
    CreateResult,
    IssueLink,
    IssueType,
    RowResult,
    SyncStatus,
    TicketSnapshot,
    WriteResult,
)
from ticketpilot.domain.reporting import ReportFilter, ReportTicket


@dataclass(frozen=True, slots=True)
class MetadataContext:
    project: str
    issue_type: IssueType
    available_fields: frozenset[str] | None = None
    required_fields: frozenset[str] = frozenset()
    # ``None`` is reserved for legacy/read-only adapters that have not resolved
    # custom-field identity. Writes containing custom fields are then blocked.
    field_ids: Mapping[str, str] | None = None
    revision: str = ""
    checked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    occurred_at: datetime
    event_type: str
    row_id: str
    outcome: str
    issue_key: str | None = None
    detail: str = ""
    dry_run: bool = True


@runtime_checkable
class JiraReadGateway(Protocol):
    def fetch_issue(self, key: str) -> TicketSnapshot:
        """Return a typed, sanitized issue snapshot."""

    def search_report_issues(self, filters: ReportFilter) -> Iterable[ReportTicket]:
        """Read all accessible tickets matching the structured report filter."""

    def list_comments(self, key: str, since: datetime | None = None) -> Iterable[Comment]:
        """Read comments; this port intentionally exposes no comment write."""


@runtime_checkable
class JiraWriteGateway(Protocol):
    def create_issue(self, payload: Mapping[str, Any]) -> CreateResult:
        """Perform one CREATE attempt; adapters must never retry POST blindly."""

    def update_issue(
        self,
        key: str,
        payload: Mapping[str, Any],
        expected_updated: datetime,
    ) -> WriteResult:
        """Update one issue after the application-level timestamp check."""

    def upload_attachment(self, key: str, attachment: Attachment) -> None:
        """Add one attachment. Deletion is deliberately absent."""

    def add_issue_link(self, key: str, link: IssueLink) -> None:
        """Add one resolved relationship. Deletion is deliberately absent."""


class JiraGateway(JiraReadGateway, JiraWriteGateway, Protocol):
    """Composite convenience port; read/write facets remain explicit above."""


@runtime_checkable
class MetadataContextProvider(Protocol):
    def get_context(
        self,
        project: str,
        issue_type: IssueType,
        *,
        force_refresh: bool = False,
    ) -> MetadataContext:
        """Resolve field IDs/context; force refresh critical data before writes."""


@runtime_checkable
class MetadataCatalogProvider(Protocol):
    def get_metadata(
        self,
        kind: str,
        *,
        project: str,
        force_refresh: bool = False,
    ) -> Any:
        """Load one small, cacheable metadata catalogue read-only."""

    def search(
        self,
        resource: str,
        query: str,
        *,
        project: str,
        start_at: int = 0,
        limit: int = 20,
    ) -> Iterable[Mapping[str, Any]]:
        """Search a large metadata resource on demand."""


class MetadataProvider(MetadataContextProvider, MetadataCatalogProvider, Protocol):
    """Composite metadata port implemented by Jira metadata adapters."""


@runtime_checkable
class ProjectPolicy(Protocol):
    def is_allowed(self, project: str) -> bool:
        """Return whether a write may target the configured project."""


@dataclass(frozen=True, slots=True)
class StaticProjectPolicy:
    allowed_projects: frozenset[str]

    def __init__(self, allowed_projects: Iterable[str]) -> None:
        object.__setattr__(
            self,
            "allowed_projects",
            frozenset(project.strip() for project in allowed_projects if project.strip()),
        )

    def is_allowed(self, project: str) -> bool:
        return project in self.allowed_projects


@runtime_checkable
class TicketRepository(Protocol):
    def get_last_result(self, row_id: str) -> RowResult | None:
        """Return the durable result used for CREATE idempotency protection."""

    def save_jira_key(self, row_id: str, issue_key: str) -> None:
        """Persist the key immediately after an unambiguous CREATE."""

    def save_result(self, result: RowResult) -> None:
        """Persist one row-level result without affecting other rows."""

    def reserve_create(self, result: RowResult) -> bool:
        """Atomically reserve a CREATE attempt before any POST is sent."""


@runtime_checkable
class CredentialStore(Protocol):
    def save(self, service: str, username: str, secret: str) -> None: ...

    def load(self, service: str, username: str) -> str | None: ...

    def delete(self, service: str, username: str) -> None: ...


@runtime_checkable
class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None:
        """Record sanitized metadata only; never raw payloads or credentials."""


class NullAuditSink:
    def record(self, event: AuditEvent) -> None:
        return None


class MemoryTicketRepository:
    """Small standard-library repository useful for UI prototyping and tests."""

    def __init__(self) -> None:
        self.results: dict[str, RowResult] = {}
        self.jira_keys: dict[str, str] = {}
        self._lock = RLock()

    def get_last_result(self, row_id: str) -> RowResult | None:
        with self._lock:
            return self.results.get(row_id)

    def save_jira_key(self, row_id: str, issue_key: str) -> None:
        with self._lock:
            self.jira_keys[row_id] = issue_key

    def save_result(self, result: RowResult) -> None:
        with self._lock:
            self.results[result.row_id] = result

    def reserve_create(self, result: RowResult) -> bool:
        with self._lock:
            previous = self.results.get(result.row_id)
            if previous is not None and previous.status in {
                SyncStatus.UNCERTAIN,
                SyncStatus.CREATED,
                SyncStatus.DUPLICATE_PREVENTED,
            }:
                return False
            self.results[result.row_id] = result
            return True
