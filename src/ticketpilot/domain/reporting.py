"""Read-only reporting filters and deterministic metric calculation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import MappingProxyType

from .models import IssueType

TODO_CATEGORY = "To Do"
IN_PROGRESS_CATEGORY = "In Progress"
DONE_CATEGORY = "Done"
UNASSIGNED_BUCKET = "(none)"


@dataclass(frozen=True, slots=True)
class ReportTicket:
    key: str
    project: str
    summary: str
    issue_type: IssueType
    status: str
    status_category: str
    created: datetime
    updated: datetime
    due_date: date | None = None
    resolved: datetime | None = None
    assignee: str | None = None
    reporter: str | None = None
    priority: str | None = None
    components: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    sprint: str | None = None
    epic: str | None = None
    labels: tuple[str, ...] = ()
    impediment: bool = False
    story_points: float | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.created.tzinfo is None or self.updated.tzinfo is None:
            raise ValueError("ReportTicket timestamps must be timezone-aware")
        if self.resolved is not None and self.resolved.tzinfo is None:
            raise ValueError("ReportTicket.resolved must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ReportFilter:
    projects: frozenset[str] = frozenset()
    sprints: frozenset[str] = frozenset()
    date_from: date | None = None
    date_to: date | None = None
    statuses: frozenset[str] = frozenset()
    status_categories: frozenset[str] = frozenset()
    issue_types: frozenset[IssueType] = frozenset()
    assignees: frozenset[str] = frozenset()
    reporters: frozenset[str] = frozenset()
    priorities: frozenset[str] = frozenset()
    components: frozenset[str] = frozenset()
    teams: frozenset[str] = frozenset()
    epics: frozenset[str] = frozenset()
    labels: frozenset[str] = frozenset()
    impediment: bool | None = None
    jira_keys: frozenset[str] = frozenset()
    free_text: str = ""


@dataclass(frozen=True, slots=True)
class ReportMetrics:
    total: int
    open: int
    in_progress: int
    done: int
    completion_rate: float
    overdue: int
    without_assignee: int
    without_sprint: int
    impediments: int
    planned_story_points: float
    completed_story_points: float
    open_story_points: float
    by_issue_type: Mapping[str, int]
    by_priority: Mapping[str, int]
    by_component: Mapping[str, int]
    by_team: Mapping[str, int]
    by_sprint: Mapping[str, int]
    tickets: tuple[ReportTicket, ...] = field(repr=False)

    @property
    def due_date_exceeded(self) -> int:
        return self.overdue


def filter_tickets(
    tickets: Iterable[ReportTicket],
    filters: ReportFilter,
) -> tuple[ReportTicket, ...]:
    """Apply all user-facing report filters without constructing raw JQL."""

    return tuple(ticket for ticket in tickets if _matches(ticket, filters))


def build_report(
    tickets: Iterable[ReportTicket],
    filters: ReportFilter | None = None,
    *,
    as_of: datetime | None = None,
) -> ReportMetrics:
    filters = filters or ReportFilter()
    as_of = as_of or datetime.now(UTC)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    selected = filter_tickets(tickets, filters)

    todo = tuple(ticket for ticket in selected if _category(ticket) == _norm(TODO_CATEGORY))
    in_progress = tuple(
        ticket for ticket in selected if _category(ticket) == _norm(IN_PROGRESS_CATEGORY)
    )
    done = tuple(ticket for ticket in selected if _category(ticket) == _norm(DONE_CATEGORY))
    overdue = tuple(
        ticket
        for ticket in selected
        if ticket.due_date is not None
        and ticket.due_date < as_of.date()
        and _category(ticket) != _norm(DONE_CATEGORY)
    )

    planned_points = sum(_points(ticket) for ticket in selected)
    completed_points = sum(_points(ticket) for ticket in done)
    open_points = sum(
        _points(ticket) for ticket in selected if _category(ticket) != _norm(DONE_CATEGORY)
    )

    return ReportMetrics(
        total=len(selected),
        open=len(todo),
        in_progress=len(in_progress),
        done=len(done),
        completion_rate=(len(done) / len(selected) * 100.0) if selected else 0.0,
        overdue=len(overdue),
        without_assignee=sum(ticket.assignee is None for ticket in selected),
        without_sprint=sum(
            ticket.sprint is None and ticket.issue_type is not IssueType.EPIC for ticket in selected
        ),
        impediments=sum(ticket.impediment for ticket in selected),
        planned_story_points=planned_points,
        completed_story_points=completed_points,
        open_story_points=open_points,
        by_issue_type=_distribution(ticket.issue_type.value for ticket in selected),
        by_priority=_distribution(ticket.priority for ticket in selected),
        by_component=_multi_distribution(ticket.components for ticket in selected),
        by_team=_multi_distribution(ticket.teams for ticket in selected),
        by_sprint=_distribution(ticket.sprint for ticket in selected),
        tickets=selected,
    )


def _matches(ticket: ReportTicket, filters: ReportFilter) -> bool:
    if filters.projects and ticket.project not in filters.projects:
        return False
    if filters.sprints and ticket.sprint not in filters.sprints:
        return False
    if filters.date_from and ticket.created.date() < filters.date_from:
        return False
    if filters.date_to and ticket.created.date() > filters.date_to:
        return False
    if filters.statuses and ticket.status not in filters.statuses:
        return False
    if filters.status_categories and ticket.status_category not in filters.status_categories:
        return False
    if filters.issue_types and ticket.issue_type not in filters.issue_types:
        return False
    if filters.assignees and ticket.assignee not in filters.assignees:
        return False
    if filters.reporters and ticket.reporter not in filters.reporters:
        return False
    if filters.priorities and ticket.priority not in filters.priorities:
        return False
    if filters.components and not filters.components.intersection(ticket.components):
        return False
    if filters.teams and not filters.teams.intersection(ticket.teams):
        return False
    if filters.epics and ticket.epic not in filters.epics:
        return False
    if filters.labels and not filters.labels.intersection(ticket.labels):
        return False
    if filters.impediment is not None and ticket.impediment is not filters.impediment:
        return False
    if filters.jira_keys and ticket.key not in filters.jira_keys:
        return False
    if filters.free_text:
        needle = filters.free_text.casefold()
        haystack = "\n".join((ticket.key, ticket.summary, ticket.description)).casefold()
        if needle not in haystack:
            return False
    return True


def _category(ticket: ReportTicket) -> str:
    return _norm(ticket.status_category)


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _points(ticket: ReportTicket) -> float:
    return float(ticket.story_points or 0.0)


def _distribution(values: Iterable[str | None]) -> Mapping[str, int]:
    counts = Counter(value if value else UNASSIGNED_BUCKET for value in values)
    return MappingProxyType(dict(sorted(counts.items())))


def _multi_distribution(values: Iterable[tuple[str, ...]]) -> Mapping[str, int]:
    counts: Counter[str] = Counter()
    for group in values:
        if group:
            counts.update(group)
        else:
            counts[UNASSIGNED_BUCKET] += 1
    return MappingProxyType(dict(sorted(counts.items())))
