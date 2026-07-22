"""Pure domain models for TicketPilot.

The module deliberately contains no knowledge of Qt, HTTP, Excel, or a specific
Jira installation.  Infrastructure adapters translate these canonical models
to and from external representations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

CLEAR_MARKER = "<CLEAR>"


class Action(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    IGNORE = "IGNORE"


class IssueType(StrEnum):
    EPIC = "Epic"
    STORY = "Story"
    BUG = "Bug"
    SERVICE_REQUEST = "Service Request"
    INCIDENT = "Incident"


class ChangeKind(StrEnum):
    SET = "SET"
    CLEAR = "CLEAR"


class LinkDirection(StrEnum):
    OUTWARD = "OUTWARD"
    INWARD = "INWARD"


class AssigneeMode(StrEnum):
    UNASSIGNED = "UNASSIGNED"
    SELF = "SELF"
    USER = "USER"


class SprintState(StrEnum):
    ACTIVE = "ACTIVE"
    FUTURE = "FUTURE"
    CLOSED = "CLOSED"


class SyncStatus(StrEnum):
    READY = "READY"
    IGNORED = "IGNORED"
    DRY_RUN = "DRY_RUN"
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"
    UNCERTAIN = "UNCERTAIN"
    DUPLICATE_PREVENTED = "DUPLICATE_PREVENTED"


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class ValidationMessage:
    code: str
    message: str
    field: str | None = None
    severity: Severity = Severity.ERROR


@dataclass(frozen=True, slots=True)
class Attachment:
    """A local attachment selected for addition.

    There is intentionally no deletion operation in the model.  ``reference``
    is an opaque local identifier or path understood by the adapter.
    """

    reference: str
    display_name: str | None = None
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class AssigneeSelection:
    """One of Jira's three supported assignment choices.

    A resolved user identifier is required only for ``USER``.  The identifier
    is deliberately opaque because Jira Data Center user identity depends on
    server metadata and version.
    """

    mode: AssigneeMode
    user_id: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class IssueLink:
    """A resolved Jira issue-link request.

    ``link_type_id`` comes from metadata; no Jira-specific ID is embedded in
    the domain.  Existing links can only be supplemented, never removed.
    """

    link_type_id: str
    target_key: str
    direction: LinkDirection = LinkDirection.OUTWARD
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class SprintRef:
    """A sprint selected from read-only Agile metadata."""

    sprint_id: str
    name: str
    state: SprintState
    board_id: str | None = None


@dataclass(frozen=True, slots=True)
class TicketCommand:
    """Canonical input from any frontend.

    ``row_id`` is a stable local operation identity and is used to prevent a
    repeated CREATE after an ambiguous response.  Frontend-specific legacy
    action names must be converted by their adapter before constructing this
    model.
    """

    row_id: str
    action: Action
    project: str
    issue_type: IssueType
    summary: Any = None
    jira_key: str | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    attachments: tuple[Attachment, ...] = ()
    links: tuple[IssueLink, ...] = ()
    sprint: SprintRef | None = None


@dataclass(frozen=True, slots=True)
class TicketSnapshot:
    """Current Jira state as translated at the infrastructure boundary."""

    key: str
    project: str
    issue_type: IssueType
    reporter: str
    status: str
    updated: datetime
    fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.updated.tzinfo is None:
            raise ValueError("TicketSnapshot.updated must be timezone-aware")


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: Any
    after: Any
    kind: ChangeKind


@dataclass(frozen=True, slots=True)
class Preview:
    preview_id: str
    command: TicketCommand
    dry_run: bool
    payload: Mapping[str, Any]
    changes: tuple[FieldChange, ...] = ()
    snapshot: TicketSnapshot | None = None
    messages: tuple[ValidationMessage, ...] = ()
    confirmation_token: str | None = None
    request_fingerprint: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("Preview.created_at must be timezone-aware")

    @property
    def errors(self) -> tuple[ValidationMessage, ...]:
        return tuple(item for item in self.messages if item.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationMessage, ...]:
        return tuple(item for item in self.messages if item.severity is Severity.WARNING)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def requires_confirmation(self) -> bool:
        return self.action_writes and not self.dry_run

    @property
    def action_writes(self) -> bool:
        return self.command.action in (Action.CREATE, Action.UPDATE)


@dataclass(frozen=True, slots=True)
class RelatedOperationResult:
    kind: str
    reference: str
    succeeded: bool
    message: str = ""
    uncertain: bool = False

    def __post_init__(self) -> None:
        if self.succeeded and self.uncertain:
            raise ValueError("A successful related operation cannot be uncertain")


@dataclass(frozen=True, slots=True)
class RowResult:
    row_id: str
    status: SyncStatus
    timestamp: datetime
    issue_key: str | None = None
    message: str = ""
    request_fingerprint: str = ""
    related: tuple[RelatedOperationResult, ...] = ()
    action: Action | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("RowResult.timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CreateResult:
    """An unambiguous successful CREATE response from a Jira adapter."""

    issue_key: str
    snapshot: TicketSnapshot | None = None


@dataclass(frozen=True, slots=True)
class WriteResult:
    """An unambiguous successful UPDATE response from a Jira adapter."""

    snapshot: TicketSnapshot | None = None


@dataclass(frozen=True, slots=True)
class Comment:
    """Read-only Jira comment representation."""

    comment_id: str
    issue_key: str
    author: str
    created: datetime
    updated: datetime
    body: str

    def __post_init__(self) -> None:
        if self.created.tzinfo is None or self.updated.tzinfo is None:
            raise ValueError("Comment timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CommentOverview:
    issue_key: str
    checked_at: datetime
    comments: tuple[Comment, ...]
    new_comment_count: int

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None:
            raise ValueError("CommentOverview.checked_at must be timezone-aware")


def is_blank(value: Any) -> bool:
    """Return whether a frontend value represents an empty cell/control.

    Whitespace is intentionally *not* blank: update semantics say that an
    empty value is unchanged and that only the exact ``<CLEAR>`` marker clears.
    Silently trimming either would change user intent.
    """

    return (
        value is None
        or value == ""
        or (isinstance(value, (list, tuple, set, frozenset)) and not value)
    )


def is_clear(value: Any) -> bool:
    return isinstance(value, str) and value == CLEAR_MARKER


def utc_now() -> datetime:
    return datetime.now(UTC)


def date_only(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value
