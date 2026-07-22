"""Central TicketPilot field schema and issue-type constraints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .models import IssueType


class ValueKind(StrEnum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"
    USER = "USER"
    MULTI_USER = "MULTI_USER"
    OPTION = "OPTION"
    MULTI_OPTION = "MULTI_OPTION"
    TIME_ESTIMATE = "TIME_ESTIMATE"
    ISSUE = "ISSUE"
    SPRINT = "SPRINT"


PROJECT = "project"
ISSUE_TYPE = "issue_type"
SUMMARY = "summary"
EPIC_NAME = "epic_name"
COMPONENTS = "components"
DESCRIPTION = "description"
PRIORITY = "priority"
PRODUCTS_SERVICES = "products_services"
ACCOUNT = "account"
LABELS = "labels"
START_DATE = "start_date"
DUE_DATE = "due_date"
STORY_POINTS = "story_points"
ASSIGNEE = "assignee"
TEAMS = "teams"
PARTICIPANTS = "participants"
EPIC_LINK = "epic_link"
PARENT_LINK = "parent_link"
ORIGINAL_ESTIMATE = "original_estimate"
REMAINING_ESTIMATE = "remaining_estimate"
IMPEDIMENT = "impediment"
FIX_VERSIONS = "fix_versions"
SPRINT = "sprint"

REPORTER = "reporter"
JIRA_KEY = "jira_key"
STATUS = "status"

IMMUTABLE_UPDATE_FIELDS = frozenset({PROJECT, ISSUE_TYPE, REPORTER, JIRA_KEY, STATUS})

ALL_ISSUE_TYPES = frozenset(IssueType)
NON_EPIC_ISSUE_TYPES = frozenset(
    {IssueType.STORY, IssueType.BUG, IssueType.SERVICE_REQUEST, IssueType.INCIDENT}
)
STORY_AND_BUG = frozenset({IssueType.STORY, IssueType.BUG})


@dataclass(frozen=True, slots=True)
class FieldRule:
    name: str
    value_kind: ValueKind
    issue_types: frozenset[IssueType] = ALL_ISSUE_TYPES
    required_for: frozenset[IssueType] = frozenset()
    clearable: bool = True
    metadata_controlled: bool = False

    def allowed_for(self, issue_type: IssueType) -> bool:
        return issue_type in self.issue_types

    def required_for_type(self, issue_type: IssueType) -> bool:
        return issue_type in self.required_for


@dataclass(frozen=True, slots=True)
class TicketSchema:
    fields: Mapping[str, FieldRule]

    def rule(self, name: str) -> FieldRule | None:
        return self.fields.get(name)

    def required_fields(self, issue_type: IssueType) -> frozenset[str]:
        return frozenset(
            name for name, rule in self.fields.items() if rule.required_for_type(issue_type)
        )

    def allowed_fields(self, issue_type: IssueType) -> frozenset[str]:
        return frozenset(name for name, rule in self.fields.items() if rule.allowed_for(issue_type))


def _rule(
    name: str,
    kind: ValueKind,
    *,
    issue_types: Iterable[IssueType] = ALL_ISSUE_TYPES,
    required_for: Iterable[IssueType] = (),
    clearable: bool = True,
    metadata_controlled: bool = False,
) -> FieldRule:
    return FieldRule(
        name=name,
        value_kind=kind,
        issue_types=frozenset(issue_types),
        required_for=frozenset(required_for),
        clearable=clearable,
        metadata_controlled=metadata_controlled,
    )


def default_ticket_schema() -> TicketSchema:
    """Return the complete canonical schema from the product requirements.

    Optional Jira custom fields remain metadata-controlled.  The only fixed
    issue-type exclusions here are explicit product rules: sprint is not for
    Epics, participants are Story/Bug only, and an Epic/Parent link is only for
    non-Epic work items.
    """

    rules = (
        _rule(PROJECT, ValueKind.TEXT, required_for=ALL_ISSUE_TYPES, clearable=False),
        _rule(ISSUE_TYPE, ValueKind.OPTION, required_for=ALL_ISSUE_TYPES, clearable=False),
        _rule(SUMMARY, ValueKind.TEXT, required_for=ALL_ISSUE_TYPES, clearable=False),
        _rule(
            EPIC_NAME,
            ValueKind.TEXT,
            issue_types=(IssueType.EPIC,),
            required_for=(IssueType.EPIC,),
            clearable=False,
            metadata_controlled=True,
        ),
        _rule(
            COMPONENTS,
            ValueKind.MULTI_OPTION,
            required_for=(IssueType.STORY, IssueType.BUG),
        ),
        _rule(DESCRIPTION, ValueKind.TEXT),
        _rule(PRIORITY, ValueKind.OPTION),
        _rule(PRODUCTS_SERVICES, ValueKind.MULTI_OPTION, metadata_controlled=True),
        _rule(ACCOUNT, ValueKind.OPTION, metadata_controlled=True),
        _rule(LABELS, ValueKind.MULTI_OPTION),
        _rule(START_DATE, ValueKind.DATE, metadata_controlled=True),
        _rule(DUE_DATE, ValueKind.DATE),
        _rule(STORY_POINTS, ValueKind.NUMBER, metadata_controlled=True),
        _rule(ASSIGNEE, ValueKind.USER),
        _rule(TEAMS, ValueKind.MULTI_OPTION, metadata_controlled=True),
        _rule(
            PARTICIPANTS,
            ValueKind.MULTI_USER,
            issue_types=STORY_AND_BUG,
            metadata_controlled=True,
        ),
        _rule(
            EPIC_LINK,
            ValueKind.ISSUE,
            issue_types=NON_EPIC_ISSUE_TYPES,
            metadata_controlled=True,
        ),
        _rule(
            PARENT_LINK,
            ValueKind.ISSUE,
            issue_types=NON_EPIC_ISSUE_TYPES,
            metadata_controlled=True,
        ),
        _rule(ORIGINAL_ESTIMATE, ValueKind.TIME_ESTIMATE),
        _rule(REMAINING_ESTIMATE, ValueKind.TIME_ESTIMATE),
        _rule(IMPEDIMENT, ValueKind.BOOLEAN, metadata_controlled=True),
        _rule(FIX_VERSIONS, ValueKind.MULTI_OPTION),
        _rule(
            SPRINT,
            ValueKind.SPRINT,
            issue_types=NON_EPIC_ISSUE_TYPES,
            metadata_controlled=True,
        ),
    )
    return TicketSchema(MappingProxyType({rule.name: rule for rule in rules}))
