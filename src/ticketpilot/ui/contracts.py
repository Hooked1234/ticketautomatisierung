"""UI-facing contracts.

The module deliberately has no Qt dependency.  The application layer can expose
its own domain objects and translate them once at the composition root, or it can
implement :class:`TicketPilotFacade` directly.  Keeping these small immutable
view records here prevents widgets from importing infrastructure code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Never, Protocol, runtime_checkable

ISSUE_TYPES = ("Epic", "Story", "Bug", "Service Request", "Incident")
ACTIONS = ("CREATE", "UPDATE", "IGNORE")


@dataclass(frozen=True, slots=True)
class SelectOption:
    value: str
    label: str
    description: str = ""
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Presentation metadata supplied by the application layer.

    ``required`` is only a visual hint.  Authoritative validation always belongs
    to the application/domain layer and is returned in ``PreviewData``.
    """

    key: str
    label: str
    editor: str = "text"
    required: bool = False
    read_only: bool = False
    clearable_on_update: bool = False
    placeholder: str = ""
    help_text: str = ""
    option_source: str | None = None
    choices: tuple[SelectOption, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupData:
    jira_url: str = "https://jira.dfd-hamburg.de"
    username: str = ""
    project: str = "DAH"
    remember_token: bool = True
    configured: bool = False
    credential_storage_available: bool = False


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    ok: bool
    headline: str
    detail: str = ""
    username: str = ""
    server_version: str = ""


@dataclass(frozen=True, slots=True)
class StartupSnapshot:
    user_display_name: str = "Nicht verbunden"
    user_account: str = ""
    project_key: str = "DAH"
    project_name: str = "Data & Analytics Hub"
    jira_url: str = "https://jira.dfd-hamburg.de"
    dry_run: bool = True
    online: bool = False
    cache_updated_at: datetime | None = None
    cache_valid_until: datetime | None = None
    board_name: str = "Nicht ausgewählt"
    pending_drafts: int = 0
    conflicts: int = 0
    last_run_at: datetime | None = None
    last_run_summary: str = "Noch keine Verarbeitung"


@dataclass(frozen=True, slots=True)
class SearchItem:
    value: str
    label: str
    subtitle: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AttachmentDraft:
    path: Path
    display_name: str = ""
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class LinkDraft:
    link_type: str
    issue_key: str
    issue_label: str = ""
    direction: str = "outward"


@dataclass(slots=True)
class TicketDraft:
    local_id: str = ""
    action: str = "CREATE"
    project: str = "DAH"
    issue_type: str = "Story"
    summary: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    attachments: list[AttachmentDraft] = field(default_factory=list)
    links: list[LinkDraft] = field(default_factory=list)
    jira_key: str = ""
    jira_updated: str = ""


@dataclass(frozen=True, slots=True)
class TicketListItem:
    local_id: str
    action: str
    project: str
    issue_type: str
    summary: str
    jira_key: str = ""
    result: str = "Entwurf"
    changed_at: datetime | None = None
    has_conflict: bool = False


@dataclass(frozen=True, slots=True)
class TicketQuery:
    text: str = ""
    action: str = "Alle"
    issue_type: str = "Alle"
    result: str = "Alle"


@dataclass(frozen=True, slots=True)
class ValidationMessage:
    severity: str
    message: str
    field: str = ""


@dataclass(frozen=True, slots=True)
class DiffItem:
    field: str
    label: str
    before: str
    after: str
    change: str = "changed"
    locked: bool = False


@dataclass(frozen=True, slots=True)
class PreviewData:
    preview_id: str
    action: str
    project: str
    issue_type: str
    summary: str
    jira_key: str = ""
    dry_run: bool = True
    valid: bool = True
    validation: tuple[ValidationMessage, ...] = ()
    diffs: tuple[DiffItem, ...] = ()
    warnings: tuple[str, ...] = ()
    attachment_names: tuple[str, ...] = ()
    link_labels: tuple[str, ...] = ()
    updated_timestamp: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    preview_ids: tuple[str, ...]
    confirmed: bool
    dry_run: bool


RelatedOutcome = Literal["SUCCESS", "FAILED", "UNCERTAIN"]


@dataclass(frozen=True, slots=True)
class RelatedItemResult:
    """Display-safe result of one attachment upload or issue-link operation.

    The facade supplies one record per attempted related operation. ``reference``
    and ``message`` are presentation values and must already be sanitized: they
    must not contain credentials, signed URLs, raw response bodies, or local
    paths. ``UNCERTAIN`` means Jira may have accepted the operation, so the UI
    must never suggest an automatic retry.
    """

    kind: str
    reference: str
    outcome: RelatedOutcome
    message: str = ""


@dataclass(frozen=True, slots=True)
class RowResult:
    row_id: str
    action: str
    summary: str
    status: str
    jira_key: str = ""
    message: str = ""
    timestamp: datetime | None = None
    conflict: bool = False
    retry_allowed: bool = False
    related: tuple[RelatedItemResult, ...] = ()
    partial_failure: bool = False
    uncertain: bool = False

    @property
    def has_partial_failure(self) -> bool:
        """Whether successful and unsuccessful sub-operations coexist."""

        outcomes = {item.outcome for item in self.related}
        derived = "SUCCESS" in outcomes and bool(outcomes & {"FAILED", "UNCERTAIN"})
        return self.partial_failure or derived

    @property
    def has_uncertain_state(self) -> bool:
        """Whether this row or one of its sub-operations has an unclear outcome."""

        return self.uncertain or any(item.outcome == "UNCERTAIN" for item in self.related)


@dataclass(frozen=True, slots=True)
class ConflictItem:
    row_id: str
    jira_key: str
    summary: str
    field: str
    local_value: str
    remote_value: str
    detected_at: datetime | None = None
    guidance: str = "Jira-Daten neu laden und die Änderung erneut prüfen."


@dataclass(frozen=True, slots=True)
class DashboardFilter:
    project: str = "DAH"
    sprint: str = ""
    date_from: date | None = None
    date_to: date | None = None
    status: str = ""
    status_category: str = ""
    issue_type: str = ""
    assignee: str = ""
    reporter: str = ""
    priority: str = ""
    component: str = ""
    team: str = ""
    epic: str = ""
    label: str = ""
    impediment: str = ""
    jira_key: str = ""
    text: str = ""


@dataclass(frozen=True, slots=True)
class MetricValue:
    key: str
    label: str
    value: str
    hint: str = ""


@dataclass(frozen=True, slots=True)
class BreakdownItem:
    label: str
    value: float
    formatted_value: str = ""


@dataclass(frozen=True, slots=True)
class DashboardData:
    metrics: tuple[MetricValue, ...] = ()
    breakdowns: Mapping[str, tuple[BreakdownItem, ...]] = field(default_factory=dict)
    generated_at: datetime | None = None
    result_count: int = 0
    scope_description: str = "Alle zugänglichen Tickets"


@dataclass(frozen=True, slots=True)
class CommentItem:
    issue_key: str
    issue_summary: str
    author: str
    created_at: datetime | None
    updated_at: datetime | None
    body: str
    is_new: bool = False


@dataclass(frozen=True, slots=True)
class CommentFilter:
    project: str = "DAH"
    text: str = ""
    issue_key: str = ""
    author: str = ""
    only_new: bool = False


@dataclass(frozen=True, slots=True)
class SettingsData:
    dry_run: bool = True
    allowed_projects: tuple[str, ...] = ("DAH",)
    selected_project: str = "DAH"
    selected_board: str = ""
    boards: tuple[SelectOption, ...] = ()
    cache_ttl_hours: int = 24
    cache_updated_at: datetime | None = None
    data_directory: str = ""
    visible_columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditItem:
    timestamp: datetime
    operation: str
    target: str
    outcome: str
    detail: str = ""
    dry_run: bool = True


ProgressCallback = Callable[[int, str], None]


@runtime_checkable
class TicketPilotFacade(Protocol):
    """Synchronous service boundary consumed by the UI worker pool.

    Implementations may perform blocking I/O: every potentially slow method is
    called from a ``QThreadPool`` worker.  Returned exceptions must already be
    stripped of secrets and raw Jira response bodies.
    """

    def startup_snapshot(self) -> StartupSnapshot: ...

    def load_setup(self) -> SetupData: ...

    def test_connection(self, jira_url: str, token: str) -> ConnectionResult: ...

    def save_setup(self, setup: SetupData, token: str) -> ConnectionResult: ...

    def refresh_metadata(self, progress: ProgressCallback | None = None) -> StartupSnapshot: ...

    def ticket_fields(self, issue_type: str, action: str) -> Sequence[FieldSpec]: ...

    def editor_options(self, issue_type: str, project: str) -> Mapping[str, Sequence[SelectOption]]: ...

    def search(self, source: str, query: str, context: Mapping[str, Any]) -> Sequence[SearchItem]: ...

    def list_tickets(self, query: TicketQuery) -> Sequence[TicketListItem]: ...

    def load_ticket(self, local_id: str) -> TicketDraft: ...

    def save_draft(self, draft: TicketDraft) -> TicketListItem: ...

    def preview(self, drafts: Sequence[TicketDraft]) -> Sequence[PreviewData]: ...

    def execute(
        self,
        request: ExecutionRequest,
        progress: ProgressCallback | None = None,
    ) -> Sequence[RowResult]: ...

    def list_results(self) -> Sequence[RowResult]: ...

    def list_conflicts(self) -> Sequence[ConflictItem]: ...

    def reload_conflict(self, row_id: str) -> TicketDraft: ...

    def dashboard_options(self) -> Mapping[str, Sequence[SelectOption]]: ...

    def load_dashboard(self, filters: DashboardFilter) -> DashboardData: ...

    def export_dashboard_csv(self, filters: DashboardFilter, destination: Path) -> Path: ...

    def list_comments(self, filters: CommentFilter) -> Sequence[CommentItem]: ...

    def load_settings(self) -> SettingsData: ...

    def save_settings(self, settings: SettingsData) -> SettingsData: ...

    def clear_metadata_cache(self) -> None: ...

    def list_audit(self, limit: int = 500) -> Sequence[AuditItem]: ...


class FacadeUnavailableError(RuntimeError):
    """Raised by the safe placeholder facade used before composition."""


class UnavailableFacade:
    """Fail-closed facade for UI smoke starts without a configured core.

    It intentionally performs no persistence and no network access.  The shell
    stays navigable, while every operation explains that a facade must be
    injected by the composition root.
    """

    _message = "Die Anwendungsdienste sind noch nicht verbunden."

    def startup_snapshot(self) -> StartupSnapshot:
        return StartupSnapshot()

    def load_setup(self) -> SetupData:
        return SetupData()

    def load_settings(self) -> SettingsData:
        return SettingsData()

    def list_tickets(self, query: TicketQuery) -> Sequence[TicketListItem]:
        return ()

    def list_results(self) -> Sequence[RowResult]:
        return ()

    def list_conflicts(self) -> Sequence[ConflictItem]:
        return ()

    def list_comments(self, filters: CommentFilter) -> Sequence[CommentItem]:
        return ()

    def list_audit(self, limit: int = 500) -> Sequence[AuditItem]:
        return ()

    def ticket_fields(self, issue_type: str, action: str) -> Sequence[FieldSpec]:
        return default_editor_fields(issue_type, action)

    def editor_options(self, issue_type: str, project: str) -> Mapping[str, Sequence[SelectOption]]:
        return {}

    def dashboard_options(self) -> Mapping[str, Sequence[SelectOption]]:
        return {}

    def _raise_unavailable(self) -> Never:
        raise FacadeUnavailableError(self._message)

    def test_connection(self, jira_url: str, token: str) -> ConnectionResult:
        return self._raise_unavailable()

    def save_setup(self, setup: SetupData, token: str) -> ConnectionResult:
        return self._raise_unavailable()

    def refresh_metadata(self, progress: ProgressCallback | None = None) -> StartupSnapshot:
        return self._raise_unavailable()

    def search(self, source: str, query: str, context: Mapping[str, Any]) -> Sequence[SearchItem]:
        return self._raise_unavailable()

    def load_ticket(self, local_id: str) -> TicketDraft:
        return self._raise_unavailable()

    def save_draft(self, draft: TicketDraft) -> TicketListItem:
        return self._raise_unavailable()

    def preview(self, drafts: Sequence[TicketDraft]) -> Sequence[PreviewData]:
        return self._raise_unavailable()

    def execute(
        self,
        request: ExecutionRequest,
        progress: ProgressCallback | None = None,
    ) -> Sequence[RowResult]:
        return self._raise_unavailable()

    def reload_conflict(self, row_id: str) -> TicketDraft:
        return self._raise_unavailable()

    def load_dashboard(self, filters: DashboardFilter) -> DashboardData:
        return self._raise_unavailable()

    def export_dashboard_csv(self, filters: DashboardFilter, destination: Path) -> Path:
        return self._raise_unavailable()

    def save_settings(self, settings: SettingsData) -> SettingsData:
        return self._raise_unavailable()

    def clear_metadata_cache(self) -> None:
        self._raise_unavailable()


def default_editor_fields(issue_type: str, action: str) -> tuple[FieldSpec, ...]:
    """Conservative visual fallback when metadata is not loaded yet.

    This is a layout fallback, not authoritative validation.  The facade's
    preview remains the only source for write eligibility.
    """

    common = [
        FieldSpec("summary", "Zusammenfassung", required=True, placeholder="Kurzer, eindeutiger Titel"),
        FieldSpec("description", "Beschreibung", editor="multiline", placeholder="Einfacher Fließtext"),
        FieldSpec("priority", "Priorität", editor="combo", option_source="priorities"),
        FieldSpec("assignee", "Zuständig", editor="search", option_source="users"),
        FieldSpec("labels", "Labels", editor="tags", option_source="labels"),
        FieldSpec("products_services", "Products and Services", editor="search-multi", option_source="products_services"),
        FieldSpec("account", "Account", editor="search", option_source="accounts"),
        FieldSpec("start_date", "Startdatum", editor="date"),
        FieldSpec("due_date", "Fälligkeitsdatum", editor="date"),
        FieldSpec("teams", "Mitarbeitende Teams", editor="search-multi", option_source="teams"),
        FieldSpec("original_estimate", "Ursprüngliche Schätzung", placeholder="z. B. 2h oder 3d"),
        FieldSpec("remaining_estimate", "Restaufwand", placeholder="z. B. 30m oder 1d"),
        FieldSpec("fix_versions", "Fix Versions", editor="multi-combo", option_source="fix_versions"),
        FieldSpec("impediment", "Markiert / Impediment", editor="boolean"),
    ]
    if issue_type in {"Story", "Bug"}:
        common.extend(
            [
                FieldSpec("components", "Komponenten", editor="multi-combo", required=True, option_source="components"),
                FieldSpec("participants", "Beteiligte", editor="search-multi", option_source="users"),
            ]
        )
    if issue_type == "Epic":
        common.append(FieldSpec("epic_name", "Epic Name", required=True))
    else:
        common.extend(
            [
                FieldSpec("epic_link", "Epic / Parent", editor="search", option_source="epics"),
                FieldSpec("sprint", "Sprint", editor="combo", option_source="sprints"),
                FieldSpec("story_points", "Story Points", editor="number"),
            ]
        )
    if action == "UPDATE":
        common.extend(
            [
                FieldSpec("jira_key", "Jira Key", read_only=True, required=True),
                FieldSpec("jira_status", "Jira-Status", read_only=True),
                FieldSpec("reporter", "Reporter", read_only=True),
            ]
        )
    return tuple(common)
