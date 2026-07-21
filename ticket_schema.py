"""Central ticket, field, action, column, and worksheet definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple


class Action(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    IGNORE = "IGNORE"


class FieldKind(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DURATION = "duration"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    USER = "user"
    MULTI_USER = "multi_user"
    ISSUE = "issue"
    ISSUE_LINK = "issue_link"
    ATTACHMENT = "attachment"


@dataclass(frozen=True)
class FieldDefinition:
    key: str
    display_name: str
    kind: FieldKind
    jira_field: Optional[str] = None
    custom_field: bool = False
    multiple: bool = False
    read_only: bool = False
    default: Any = None
    worksheet_column: bool = True


@dataclass(frozen=True)
class IssueTypeSchema:
    name: str
    required_fields: Tuple[str, ...]
    optional_fields: Tuple[str, ...]
    supports_sprint: bool = False

    @property
    def available_fields(self) -> Tuple[str, ...]:
        fields = self.required_fields + self.optional_fields
        if self.supports_sprint:
            fields += ("Sprint",)
        return tuple(dict.fromkeys(fields))


def _field(
    key: str,
    display_name: str,
    kind: FieldKind,
    jira_field: Optional[str] = None,
    **kwargs: Any,
) -> FieldDefinition:
    return FieldDefinition(
        key=key,
        display_name=display_name,
        kind=kind,
        jira_field=jira_field,
        **kwargs,
    )


FIELD_DEFINITIONS: Mapping[str, FieldDefinition] = MappingProxyType(
    {
        "Project": _field("Project", "Project", FieldKind.SINGLE_SELECT, "project"),
        "IssueType": _field(
            "IssueType", "Issue Type", FieldKind.SINGLE_SELECT, "issuetype"
        ),
        "Summary": _field("Summary", "Summary", FieldKind.TEXT, "summary"),
        "EpicName": _field(
            "EpicName", "Epic Name", FieldKind.TEXT, custom_field=True
        ),
        "Priority": _field(
            "Priority", "Priority", FieldKind.SINGLE_SELECT, "priority"
        ),
        "ProductsAndServices": _field(
            "ProductsAndServices",
            "Products and Services",
            FieldKind.SINGLE_SELECT,
            custom_field=True,
        ),
        "Components": _field(
            "Components",
            "Component/s",
            FieldKind.MULTI_SELECT,
            "components",
            multiple=True,
        ),
        "Account": _field(
            "Account", "Account", FieldKind.SINGLE_SELECT, custom_field=True
        ),
        "Labels": _field(
            "Labels", "Labels", FieldKind.MULTI_SELECT, "labels", multiple=True
        ),
        "Attachment": _field(
            "Attachment",
            "Attachment",
            FieldKind.ATTACHMENT,
            worksheet_column=False,
        ),
        "DueDate": _field("DueDate", "Due Date", FieldKind.DATE, "duedate"),
        "StartDate": _field(
            "StartDate", "Start Date", FieldKind.DATE, custom_field=True
        ),
        "EndDate": _field(
            "EndDate", "End Date", FieldKind.DATE, custom_field=True
        ),
        "Description": _field(
            "Description", "Description", FieldKind.TEXT, "description"
        ),
        "Reporter": _field(
            "Reporter", "Reporter", FieldKind.USER, "reporter", read_only=True
        ),
        "Assignee": _field("Assignee", "Assignee", FieldKind.USER, "assignee"),
        "Participants": _field(
            "Participants",
            "Beteiligte",
            FieldKind.MULTI_USER,
            custom_field=True,
            multiple=True,
        ),
        "ResponsibleTeam": _field(
            "ResponsibleTeam",
            "Responsible Team",
            FieldKind.SINGLE_SELECT,
            custom_field=True,
        ),
        "CollaboratingTeams": _field(
            "CollaboratingTeams",
            "Mitarbeitende Teams",
            FieldKind.MULTI_SELECT,
            custom_field=True,
            multiple=True,
        ),
        "ParentLink": _field(
            "ParentLink", "Parent Link", FieldKind.ISSUE, custom_field=True
        ),
        "LinkedIssues": _field(
            "LinkedIssues",
            "Linked Issues",
            FieldKind.ISSUE_LINK,
            worksheet_column=False,
        ),
        "Impediment": _field(
            "Impediment",
            "Markiert / Impediment",
            FieldKind.BOOLEAN,
            custom_field=True,
            default=False,
        ),
        "FixVersions": _field(
            "FixVersions",
            "Fix Version/s",
            FieldKind.MULTI_SELECT,
            "fixVersions",
            multiple=True,
        ),
        "StoryPoints": _field(
            "StoryPoints", "Story Points", FieldKind.NUMBER, custom_field=True
        ),
        "EpicLink": _field(
            "EpicLink", "Epic Link", FieldKind.ISSUE, custom_field=True
        ),
        "OriginalEstimate": _field(
            "OriginalEstimate", "Original Estimate", FieldKind.DURATION
        ),
        "RemainingEstimate": _field(
            "RemainingEstimate", "Remaining Estimate", FieldKind.DURATION
        ),
        "Sprint": _field(
            "Sprint", "Sprint", FieldKind.SINGLE_SELECT, custom_field=True
        ),
        "JiraStatus": _field(
            "JiraStatus",
            "Jira Status",
            FieldKind.SINGLE_SELECT,
            "status",
            read_only=True,
        ),
        "JiraUpdated": _field(
            "JiraUpdated",
            "Jira Updated",
            FieldKind.TEXT,
            "updated",
            read_only=True,
        ),
    }
)


ISSUE_TYPE_SCHEMAS: Mapping[str, IssueTypeSchema] = MappingProxyType(
    {
        "Epic": IssueTypeSchema(
            name="Epic",
            required_fields=("Project", "IssueType", "Summary", "EpicName"),
            optional_fields=(
                "Priority",
                "ProductsAndServices",
                "Components",
                "Account",
                "Labels",
                "Attachment",
                "DueDate",
                "StartDate",
                "EndDate",
                "Description",
                "Reporter",
                "Assignee",
                "Participants",
                "ResponsibleTeam",
                "ParentLink",
                "LinkedIssues",
                "Impediment",
                "FixVersions",
            ),
        ),
        "Story": IssueTypeSchema(
            name="Story",
            required_fields=("Project", "IssueType", "Summary", "Components"),
            optional_fields=(
                "Priority",
                "ProductsAndServices",
                "Account",
                "Labels",
                "Description",
                "Attachment",
                "StoryPoints",
                "DueDate",
                "StartDate",
                "EndDate",
                "Assignee",
                "Participants",
                "CollaboratingTeams",
                "EpicLink",
                "LinkedIssues",
                "OriginalEstimate",
                "RemainingEstimate",
            ),
            supports_sprint=True,
        ),
        "Bug": IssueTypeSchema(
            name="Bug",
            required_fields=("Project", "IssueType", "Summary", "Components"),
            optional_fields=(
                "Priority",
                "ProductsAndServices",
                "Account",
                "Labels",
                "Description",
                "Attachment",
                "StoryPoints",
                "DueDate",
                "StartDate",
                "EndDate",
                "Assignee",
                "Participants",
                "CollaboratingTeams",
                "EpicLink",
                "LinkedIssues",
                "OriginalEstimate",
                "RemainingEstimate",
            ),
            supports_sprint=True,
        ),
        "Service Request": IssueTypeSchema(
            name="Service Request",
            required_fields=("Project", "IssueType", "Summary"),
            optional_fields=(
                "Priority",
                "ProductsAndServices",
                "Components",
                "Account",
                "Labels",
                "Description",
                "Attachment",
                "StoryPoints",
                "DueDate",
                "StartDate",
                "EndDate",
                "Assignee",
                "CollaboratingTeams",
                "EpicLink",
                "LinkedIssues",
                "OriginalEstimate",
                "RemainingEstimate",
            ),
            supports_sprint=True,
        ),
        "Incident": IssueTypeSchema(
            name="Incident",
            required_fields=("Project", "IssueType", "Summary"),
            optional_fields=(
                "Priority",
                "ProductsAndServices",
                "Components",
                "Account",
                "Labels",
                "Description",
                "Attachment",
                "StoryPoints",
                "DueDate",
                "StartDate",
                "EndDate",
                "Assignee",
                "CollaboratingTeams",
                "EpicLink",
                "LinkedIssues",
                "OriginalEstimate",
                "RemainingEstimate",
            ),
            supports_sprint=True,
        ),
    }
)

SUPPORTED_ACTIONS = tuple(action.value for action in Action)
SUPPORTED_ISSUE_TYPES = tuple(ISSUE_TYPE_SCHEMAS)
DEFAULT_VISIBLE_COLUMNS = (
    "Action",
    "Project",
    "IssueType",
    "Summary",
    "Description",
    "Priority",
    "Components",
    "Assignee",
    "EpicLink",
    "Sprint",
    "JiraKey",
    "Result",
    "ErrorMessage",
)
OPTIONAL_TICKET_COLUMNS = (
    "EpicName",
    "ProductsAndServices",
    "Account",
    "Labels",
    "StoryPoints",
    "DueDate",
    "StartDate",
    "EndDate",
    "Reporter",
    "Participants",
    "CollaboratingTeams",
    "ResponsibleTeam",
    "ParentLink",
    "Impediment",
    "FixVersions",
    "OriginalEstimate",
    "RemainingEstimate",
    "JiraStatus",
    "JiraUpdated",
)
LEGACY_TICKET_COLUMNS = ("Status", "CreatedAt", "ExternalId")
ALL_TICKET_COLUMNS = tuple(
    dict.fromkeys(
        DEFAULT_VISIBLE_COLUMNS + OPTIONAL_TICKET_COLUMNS + LEGACY_TICKET_COLUMNS
    )
)
WORKBOOK_SHEETS = (
    "Tickets",
    "Attachments",
    "Ticket_Links",
    "Einstellungen",
    "Metadaten",
    "Sprints",
    "Kommentare",
    "Sync_Log",
    "Konflikte",
    "Dashboard",
)
LEGACY_STATUS_ACTIONS: Mapping[str, Action] = MappingProxyType(
    {
        "NEU": Action.CREATE,
        "EDIT": Action.UPDATE,
        "ALT": Action.IGNORE,
        "FEHLER": Action.IGNORE,
    }
)


def normalize_action(value: Any) -> Action:
    normalized = str(value or "").strip().upper()
    try:
        return Action(normalized)
    except ValueError as error:
        allowed = ", ".join(SUPPORTED_ACTIONS)
        raise ValueError(
            f"Ungueltige Action '{value or ''}'. Erlaubt: {allowed}."
        ) from error


def action_from_legacy_status(value: Any) -> Optional[Action]:
    return LEGACY_STATUS_ACTIONS.get(str(value or "").strip().upper())


def normalize_issue_type(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    for issue_type in SUPPORTED_ISSUE_TYPES:
        if issue_type.casefold() == normalized:
            return issue_type
    allowed = ", ".join(SUPPORTED_ISSUE_TYPES)
    raise ValueError(
        f"Unbekannter Issue Type '{value or ''}'. Unterstuetzt: {allowed}."
    )


def get_issue_type_schema(issue_type: Any) -> IssueTypeSchema:
    return ISSUE_TYPE_SCHEMAS[normalize_issue_type(issue_type)]
