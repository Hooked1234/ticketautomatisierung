"""Offline gateways and the explicit seam for a future Jira adapter.

No class in this module imports an HTTP client or performs network I/O.
``DisabledJiraGateway`` is the production-safe default until composition code
explicitly supplies a separately implemented, authenticated Jira adapter.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, NoReturn, Protocol, runtime_checkable

from ticketpilot.application.errors import ConcurrencyConflict
from ticketpilot.application.ports import MetadataContext
from ticketpilot.domain.models import (
    CLEAR_MARKER,
    Attachment,
    Comment,
    CreateResult,
    IssueLink,
    IssueType,
    TicketSnapshot,
    WriteResult,
)
from ticketpilot.domain.reporting import ReportFilter, ReportTicket, filter_tickets
from ticketpilot.domain.schema import default_ticket_schema


class GatewayError(RuntimeError):
    """Safe base error for adapter-boundary failures."""


class GatewayDisabledError(GatewayError):
    """Raised when Jira integration has not been configured yet."""


class GatewayNotFoundError(GatewayError):
    """Raised when a synthetic or external object cannot be found."""


class GatewayValidationError(GatewayError):
    """Raised before an invalid adapter operation can be attempted."""


@runtime_checkable
class JiraGatewayAdapter(Protocol):
    """Complete adapter contract expected by the current application ports."""

    def fetch_issue(self, key: str) -> TicketSnapshot: ...

    def search_report_issues(self, filters: ReportFilter) -> Iterable[ReportTicket]: ...

    def list_comments(self, key: str, since: datetime | None = None) -> Iterable[Comment]: ...

    def create_issue(self, payload: Mapping[str, Any]) -> CreateResult: ...

    def update_issue(
        self,
        key: str,
        payload: Mapping[str, Any],
        expected_updated: datetime,
    ) -> WriteResult: ...

    def upload_attachment(self, key: str, attachment: Attachment) -> None: ...

    def add_issue_link(self, key: str, link: IssueLink) -> None: ...


@runtime_checkable
class MetadataGatewayAdapter(Protocol):
    def get_context(
        self,
        project: str,
        issue_type: IssueType,
        *,
        force_refresh: bool = False,
    ) -> MetadataContext: ...

    def get_metadata(
        self,
        kind: str,
        *,
        project: str = "DAH",
        force_refresh: bool = False,
    ) -> Any: ...


_LOCKED_UPDATE_FIELDS = frozenset({"project", "issue_type", "reporter", "jira_key", "status"})


class DisabledJiraGateway:
    """Fail-closed adapter used until the user connects Jira in a later step."""

    enabled = False
    synthetic = False

    def __init__(self, reason: str = "Jira-Verbindung ist noch nicht eingerichtet.") -> None:
        self.reason = reason

    def test_connection(self) -> dict[str, Any]:
        return {
            "ok": False,
            "connected": False,
            "mode": "disabled",
            "message": self.reason,
        }

    def fetch_issue(self, key: str) -> TicketSnapshot:
        self._disabled()

    def search_report_issues(self, filters: ReportFilter) -> Iterable[ReportTicket]:
        self._disabled()

    def list_comments(self, key: str, since: datetime | None = None) -> Iterable[Comment]:
        self._disabled()

    def create_issue(self, payload: Mapping[str, Any]) -> CreateResult:
        self._disabled()

    def update_issue(
        self,
        key: str,
        payload: Mapping[str, Any],
        expected_updated: datetime,
    ) -> WriteResult:
        self._disabled()

    def upload_attachment(self, key: str, attachment: Attachment) -> None:
        self._disabled()

    def add_issue_link(self, key: str, link: IssueLink) -> None:
        self._disabled()

    def get_context(
        self,
        project: str,
        issue_type: IssueType,
        *,
        force_refresh: bool = False,
    ) -> MetadataContext:
        self._disabled()

    def get_metadata(
        self,
        kind: str,
        *,
        project: str = "DAH",
        force_refresh: bool = False,
    ) -> Any:
        self._disabled()

    def search(
        self,
        resource: str,
        query: str,
        *,
        project: str = "DAH",
        start_at: int = 0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self._disabled()

    def _disabled(self) -> NoReturn:
        raise GatewayDisabledError(self.reason)


class LocalDemoGateway:
    """Deterministic, in-memory Jira substitute with synthetic DAH data.

    Demo writes mutate only this instance.  They cannot be confused with Jira
    I/O because the adapter owns no URL, credential or network dependency.
    """

    enabled = True
    synthetic = True

    def __init__(
        self,
        *,
        project_allowlist: Iterable[str] = ("DAH",),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_allowlist = frozenset(str(item).strip().upper() for item in project_allowlist)
        if not self.project_allowlist:
            raise ValueError("Demo project allowlist must not be empty")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._attachments: dict[str, list[Attachment]] = {}
        self._links: dict[str, list[IssueLink]] = {}
        self._metadata = _demo_metadata()
        self._issues = _demo_issues()
        self._comments = _demo_comments()
        self._next_sequence = 90010

    def test_connection(self) -> dict[str, Any]:
        return {
            "ok": True,
            "connected": True,
            "mode": "local-demo",
            "synthetic": True,
            "username": "demo.user",
            "display_name": "Demo User",
            "server_version": "offline-synthetic",
            "message": "Lokaler Demomodus – keine Jira-Verbindung.",
        }

    def set_project_allowlist(self, projects: Iterable[str]) -> None:
        """Update local policy without discarding synthetic issues or sequence state."""

        normalized = frozenset(str(item).strip().upper() for item in projects if str(item).strip())
        if not normalized:
            raise ValueError("Demo project allowlist must not be empty")
        with self._lock:
            self.project_allowlist = normalized

    def hydrate_issues(self, snapshots: Iterable[TicketSnapshot]) -> None:
        """Restore durable synthetic issues without weakening demo isolation."""

        with self._lock:
            highest = self._next_sequence - 1
            for snapshot in snapshots:
                if snapshot.project not in self.project_allowlist:
                    continue
                self._issues[snapshot.key] = copy.deepcopy(snapshot)
                suffix = snapshot.key.rsplit("-", 1)[-1]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
            self._next_sequence = highest + 1

    def fetch_issue(self, key: str) -> TicketSnapshot:
        normalized = _required_text(key, "Jira key").upper()
        with self._lock:
            snapshot = self._issues.get(normalized)
            if snapshot is None:
                raise GatewayNotFoundError(f"Ticket {normalized} wurde im Demomodus nicht gefunden")
            return copy.deepcopy(snapshot)

    # Alias for callers using the older read-oriented name.
    read_issue = fetch_issue

    def create_issue(self, payload: Mapping[str, Any]) -> CreateResult:
        project = _project_value(payload.get("project", "DAH"))
        self._require_project(project)
        issue_type = _issue_type_value(payload.get("issue_type", IssueType.STORY))
        summary = str(payload.get("summary", "")).strip()
        if not summary:
            raise GatewayValidationError("Summary ist für ein Demo-Ticket erforderlich")
        now = _as_utc(self._clock())
        with self._lock:
            key = f"{project}-{self._next_sequence}"
            self._next_sequence += 1
            fields = _demo_snapshot_fields(payload)
            fields.pop("project", None)
            fields.pop("issue_type", None)
            fields.setdefault("summary", summary)
            snapshot = TicketSnapshot(
                key=key,
                project=project,
                issue_type=issue_type,
                reporter="demo.user",
                status="Open",
                updated=now,
                fields=fields,
            )
            self._issues[key] = snapshot
        return CreateResult(issue_key=key, snapshot=copy.deepcopy(snapshot))

    def update_issue(
        self,
        key: str,
        payload: Mapping[str, Any],
        expected_updated: datetime,
    ) -> WriteResult:
        if expected_updated.tzinfo is None:
            raise GatewayValidationError("Expected updated timestamp must be timezone-aware")
        forbidden = sorted(_LOCKED_UPDATE_FIELDS.intersection(payload))
        if forbidden:
            raise GatewayValidationError(
                f"Gesperrte Felder dürfen nicht geändert werden: {', '.join(forbidden)}"
            )
        normalized = _required_text(key, "Jira key").upper()
        with self._lock:
            current = self._issues.get(normalized)
            if current is None:
                raise GatewayNotFoundError(f"Ticket {normalized} wurde im Demomodus nicht gefunden")
            if current.updated != _as_utc(expected_updated):
                raise ConcurrencyConflict(
                    "Das Demo-Ticket wurde seit der Vorschau geändert und muss neu geladen werden."
                )
            fields = copy.deepcopy(dict(current.fields))
            for name, value in payload.items():
                if value is None or value == CLEAR_MARKER:
                    fields.pop(name, None)
                elif value is not None and value != "":
                    stored = _demo_snapshot_value(name, value)
                    if stored is None:
                        fields.pop(name, None)
                    else:
                        fields[name] = stored
            updated = _as_utc(self._clock())
            if updated <= current.updated:
                updated = current.updated + timedelta(microseconds=1)
            snapshot = TicketSnapshot(
                key=current.key,
                project=current.project,
                issue_type=current.issue_type,
                reporter=current.reporter,
                status=current.status,
                updated=updated,
                fields=fields,
            )
            self._issues[normalized] = snapshot
        return WriteResult(snapshot=copy.deepcopy(snapshot))

    def upload_attachment(self, key: str, attachment: Attachment) -> None:
        normalized = self.fetch_issue(key).key
        if not attachment.reference.strip():
            raise GatewayValidationError("Anhangsreferenz darf nicht leer sein")
        with self._lock:
            self._attachments.setdefault(normalized, []).append(copy.deepcopy(attachment))

    def add_issue_link(self, key: str, link: IssueLink) -> None:
        normalized = self.fetch_issue(key).key
        self.fetch_issue(link.target_key)
        with self._lock:
            self._links.setdefault(normalized, []).append(copy.deepcopy(link))

    def list_uploaded_attachments(self, key: str) -> tuple[Attachment, ...]:
        normalized = self.fetch_issue(key).key
        with self._lock:
            return tuple(copy.deepcopy(self._attachments.get(normalized, ())))

    def list_added_links(self, key: str) -> tuple[IssueLink, ...]:
        normalized = self.fetch_issue(key).key
        with self._lock:
            return tuple(copy.deepcopy(self._links.get(normalized, ())))

    def list_comments(self, key: str, since: datetime | None = None) -> tuple[Comment, ...]:
        normalized = self.fetch_issue(key).key
        if since is not None and since.tzinfo is None:
            raise GatewayValidationError("Comment timestamp must be timezone-aware")
        threshold = _as_utc(since) if since is not None else None
        with self._lock:
            comments = self._comments.get(normalized, ())
            return tuple(
                copy.deepcopy(comment)
                for comment in comments
                if threshold is None or comment.created > threshold
            )

    def search_report_issues(self, filters: ReportFilter) -> tuple[ReportTicket, ...]:
        with self._lock:
            tickets = tuple(_snapshot_to_report(item) for item in self._issues.values())
        return filter_tickets(tickets, filters)

    def get_context(
        self,
        project: str,
        issue_type: IssueType,
        *,
        force_refresh: bool = False,
    ) -> MetadataContext:
        normalized_project = _required_text(project, "project").upper()
        self._require_project(normalized_project)
        normalized_type = _issue_type_value(issue_type)
        schema = default_ticket_schema()
        available = schema.allowed_fields(normalized_type)
        field_ids = {name: f"demo:{name}" for name in available}
        return MetadataContext(
            project=normalized_project,
            issue_type=normalized_type,
            available_fields=available,
            required_fields=schema.required_fields(normalized_type),
            field_ids=field_ids,
            revision="demo-metadata-v1",
            checked_at=_as_utc(self._clock()),
        )

    def validate_metadata(
        self,
        project: str,
        issue_type: IssueType | str,
        fields: Iterable[str] = (),
    ) -> tuple[str, ...]:
        context = self.get_context(project, _issue_type_value(issue_type), force_refresh=True)
        available = context.available_fields or frozenset()
        return tuple(sorted(name for name in fields if name not in available))

    def get_metadata(
        self,
        kind: str,
        *,
        project: str = "DAH",
        force_refresh: bool = False,
    ) -> Any:
        self._require_project(project)
        normalized = _metadata_kind(kind)
        if normalized not in self._metadata:
            raise GatewayNotFoundError(f"Unbekannte Demo-Metadatenart: {kind}")
        return copy.deepcopy(self._metadata[normalized])

    def search(
        self,
        resource: str,
        query: str,
        *,
        project: str = "DAH",
        start_at: int = 0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self._require_project(project)
        if start_at < 0 or not 1 <= limit <= 100:
            raise ValueError("Search pagination is invalid")
        normalized = _metadata_kind(resource)
        with self._lock:
            sources = {
                "people": self._metadata["people"],
                "epics": [
                    {
                        "id": issue.key,
                        "value": issue.key,
                        "label": issue.fields.get("summary", ""),
                    }
                    for issue in self._issues.values()
                    if issue.issue_type is IssueType.EPIC
                ],
                "issues": [
                    {
                        "id": issue.key,
                        "value": issue.key,
                        "label": issue.fields.get("summary", ""),
                    }
                    for issue in self._issues.values()
                ],
                "products_services": self._metadata["products_services"],
                "accounts": self._metadata["accounts"],
            }
        if normalized not in sources:
            raise GatewayNotFoundError(f"Diese Demo-Suche wird nicht unterstützt: {resource}")
        needle = query.strip().casefold()
        matches = [
            copy.deepcopy(item)
            for item in sources[normalized]
            if not needle or needle in " ".join(str(value) for value in item.values()).casefold()
        ]
        return matches[start_at : start_at + limit]

    def _require_project(self, project: object) -> None:
        normalized = _required_text(project, "project").upper()
        if normalized not in self.project_allowlist:
            raise GatewayValidationError(f"Projekt {normalized} ist nicht freigegeben")


def _demo_metadata() -> dict[str, list[dict[str, Any]]]:
    return {
        "issue_types": [
            {"id": f"demo:{item.name.lower()}", "value": item.value, "label": item.value}
            for item in IssueType
        ],
        "priorities": [
            {"id": "demo:high", "value": "High", "label": "High"},
            {"id": "demo:medium", "value": "Medium", "label": "Medium"},
            {"id": "demo:low", "value": "Low", "label": "Low"},
        ],
        "components": [
            {"id": "demo:data", "value": "Data Platform", "label": "Data Platform"},
            {"id": "demo:bi", "value": "BI", "label": "BI"},
            {"id": "demo:ops", "value": "Operations", "label": "Operations"},
        ],
        "teams": [
            {"id": "demo:analytics", "value": "Analytics", "label": "Analytics"},
            {"id": "demo:engineering", "value": "Engineering", "label": "Engineering"},
        ],
        "link_types": [
            {"id": "demo:blocks", "value": "demo:blocks", "label": "blocks"},
            {"id": "demo:relates", "value": "demo:relates", "label": "relates to"},
        ],
        "boards": [
            {"id": "demo:board:1", "value": "demo:board:1", "label": "DAH Demo Board"},
            {
                "id": "demo:board:2",
                "value": "demo:board:2",
                "label": "DAH Service Board",
            },
        ],
        "sprints": [
            {
                "id": "demo:sprint:active",
                "value": "demo:sprint:active",
                "label": "Demo Sprint aktuell",
                "state": "ACTIVE",
                "board_id": "demo:board:1",
            },
            {
                "id": "demo:sprint:future",
                "value": "demo:sprint:future",
                "label": "Demo Sprint nächster",
                "state": "FUTURE",
                "board_id": "demo:board:1",
            },
            {
                "id": "demo:sprint:service",
                "value": "demo:sprint:service",
                "label": "Service Sprint aktuell",
                "state": "ACTIVE",
                "board_id": "demo:board:2",
            },
        ],
        "fix_versions": [
            {"id": "demo:v1", "value": "Pilot 1", "label": "Pilot 1"},
            {"id": "demo:v2", "value": "Pilot 2", "label": "Pilot 2"},
        ],
        "people": [
            {"id": "demo.user", "value": "demo.user", "label": "Demo User"},
            {"id": "alex.example", "value": "alex.example", "label": "Alex Beispiel"},
            {"id": "sam.example", "value": "sam.example", "label": "Sam Beispiel"},
        ],
        "products_services": [
            {"id": "demo:warehouse", "value": "Data Warehouse", "label": "Data Warehouse"},
            {"id": "demo:reporting", "value": "Reporting", "label": "Reporting"},
        ],
        "accounts": [
            {"id": "demo:none", "value": "", "label": "None"},
            {"id": "demo:internal", "value": "Internal", "label": "Internal"},
        ],
    }


def _demo_issues() -> dict[str, TicketSnapshot]:
    now = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)
    source = (
        TicketSnapshot(
            key="DAH-90001",
            project="DAH",
            issue_type=IssueType.STORY,
            reporter="demo.user",
            status="In Progress",
            updated=now - timedelta(hours=2),
            fields={
                "summary": "Vertriebsdashboard erweitern",
                "description": "Vollständig synthetisches Demoticket.",
                "priority": "High",
                "components": ["BI"],
                "teams": ["Analytics"],
                # Sprint identity is kept as the metadata ID throughout the
                # write/read pipeline.  The UI resolves the human label from
                # the same catalogue, which avoids a false name -> ID change
                # when an existing issue is opened for UPDATE.
                "sprint": "demo:sprint:active",
                "story_points": 5,
                "assignee": "alex.example",
                "created": now - timedelta(days=8),
                "status_category": "In Progress",
                "due_date": date(2026, 7, 28),
            },
        ),
        TicketSnapshot(
            key="DAH-90002",
            project="DAH",
            issue_type=IssueType.BUG,
            reporter="demo.user",
            status="Done",
            updated=now - timedelta(days=1),
            fields={
                "summary": "Fehlerhafte Beispielsummen korrigieren",
                "description": "Vollständig synthetisches Demoticket.",
                "priority": "Medium",
                "components": ["Data Platform"],
                "teams": ["Engineering"],
                "sprint": "demo:sprint:active",
                "story_points": 3,
                "assignee": "sam.example",
                "created": now - timedelta(days=10),
                "resolved": now - timedelta(days=1),
                "status_category": "Done",
            },
        ),
        TicketSnapshot(
            key="DAH-90003",
            project="DAH",
            issue_type=IssueType.EPIC,
            reporter="demo.user",
            status="Open",
            updated=now - timedelta(days=2),
            fields={
                "summary": "Demo Analytics Modernisierung",
                "epic_name": "Analytics Demo",
                "priority": "Medium",
                "created": now - timedelta(days=20),
                "status_category": "To Do",
            },
        ),
        TicketSnapshot(
            key="DAH-90004",
            project="DAH",
            issue_type=IssueType.INCIDENT,
            reporter="demo.user",
            status="Open",
            updated=now - timedelta(hours=6),
            fields={
                "summary": "Synthetischer Pipeline-Alarm",
                "description": "Keine echte Störung.",
                "priority": "High",
                "teams": ["Engineering"],
                "created": now - timedelta(days=4),
                "status_category": "To Do",
                "due_date": date(2026, 7, 20),
                "impediment": True,
            },
        ),
    )
    return {item.key: item for item in source}


def _demo_comments() -> dict[str, tuple[Comment, ...]]:
    first = datetime(2026, 7, 20, 13, 0, tzinfo=UTC)
    second = datetime(2026, 7, 21, 8, 30, tzinfo=UTC)
    return {
        "DAH-90001": (
            Comment(
                "demo-comment-1",
                "DAH-90001",
                "Alex Beispiel",
                first,
                first,
                "Demo: Analyse begonnen.",
            ),
            Comment(
                "demo-comment-2",
                "DAH-90001",
                "Demo User",
                second,
                second,
                "Demo: Rückfrage geklärt.",
            ),
        )
    }


def _snapshot_to_report(snapshot: TicketSnapshot) -> ReportTicket:
    fields = snapshot.fields
    created = fields.get("created")
    if not isinstance(created, datetime):
        created = snapshot.updated
    resolved = fields.get("resolved")
    due_date = fields.get("due_date")
    if isinstance(due_date, datetime):
        due_date = due_date.date()
    return ReportTicket(
        key=snapshot.key,
        project=snapshot.project,
        summary=str(fields.get("summary", "")),
        issue_type=snapshot.issue_type,
        status=snapshot.status,
        status_category=str(fields.get("status_category", "To Do")),
        created=_as_utc(created),
        updated=snapshot.updated,
        due_date=due_date if isinstance(due_date, date) else None,
        resolved=_as_utc(resolved) if isinstance(resolved, datetime) else None,
        assignee=_optional_text(fields.get("assignee")),
        reporter=snapshot.reporter,
        priority=_optional_text(fields.get("priority")),
        components=_string_tuple(fields.get("components")),
        teams=_string_tuple(fields.get("teams")),
        sprint=_optional_text(fields.get("sprint")),
        epic=_optional_text(fields.get("epic_link")),
        labels=_string_tuple(fields.get("labels")),
        impediment=bool(fields.get("impediment", False)),
        story_points=_optional_float(fields.get("story_points")),
        description=str(fields.get("description", "")),
    )


def _project_value(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("key", value.get("value", ""))
    return _required_text(value, "project").upper()


def _demo_snapshot_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Translate adapter payload values back into Jira-like read values."""

    fields: dict[str, Any] = {}
    for name, value in payload.items():
        if value is None or value == CLEAR_MARKER:
            continue
        stored = _demo_snapshot_value(str(name), value)
        if stored is not None:
            fields[str(name)] = stored
    return fields


def _demo_snapshot_value(name: str, value: Any) -> Any:
    if name != "assignee" or not isinstance(value, Mapping):
        return copy.deepcopy(value)
    mode = str(value.get("mode", "")).strip().upper()
    if mode == "USER":
        user_id = str(value.get("user_id", "")).strip()
        if not user_id:
            raise GatewayValidationError("Resolved assignee user ID is required")
        return user_id
    if mode == "SELF":
        return "demo.user"
    if mode == "UNASSIGNED":
        return None
    raise GatewayValidationError("Unknown assignee selection mode")


def _issue_type_value(value: Any) -> IssueType:
    if isinstance(value, IssueType):
        return value
    if isinstance(value, Mapping):
        value = value.get("name", value.get("value", ""))
    text = _required_text(value, "issue type")
    try:
        return IssueType(text)
    except ValueError as error:
        raise GatewayValidationError(f"Nicht unterstützter Issue Type: {text}") from error


def _metadata_kind(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "issuetypes": "issue_types",
        "linktypes": "link_types",
        "versions": "fix_versions",
        "fixversions": "fix_versions",
        "users": "people",
        "products_and_services": "products_services",
        "tickets": "issues",
    }
    return aliases.get(normalized, normalized)


def _required_text(value: object, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise GatewayValidationError(f"{name} darf nicht leer sein")
    return result


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return (str(value),)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DisabledJiraGateway",
    "GatewayDisabledError",
    "GatewayError",
    "GatewayNotFoundError",
    "GatewayValidationError",
    "JiraGatewayAdapter",
    "LocalDemoGateway",
    "MetadataGatewayAdapter",
]
