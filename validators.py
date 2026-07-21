"""Ticket validation independent from Excel and Jira transports."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from numbers import Number
from typing import Any, List, Optional

from ticket_schema import (
    Action,
    FIELD_DEFINITIONS,
    action_from_legacy_status,
    get_issue_type_schema,
    normalize_action,
    normalize_issue_type,
)


CLEAR_SENTINEL = "<CLEAR>"
_JIRA_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
_JIRA_DURATION_PATTERN = re.compile(
    r"^[0-9]+(?:[.,][0-9]+)?[mhdw](?:\s+[0-9]+(?:[.,][0-9]+)?[mhdw])*$",
    re.IGNORECASE,
)


class TicketValidationError(ValueError):
    """Raised for a single invalid workbook value."""


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def is_clear(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == CLEAR_SENTINEL


def resolve_action(row: Mapping[str, Any]) -> Action:
    value = row.get("Action")
    if not is_blank(value):
        try:
            return normalize_action(value)
        except ValueError as error:
            raise TicketValidationError(str(error)) from error

    legacy_action = action_from_legacy_status(row.get("Status"))
    if legacy_action is not None:
        return legacy_action
    raise TicketValidationError(
        "Action fehlt. Erlaubt: CREATE, UPDATE oder IGNORE."
    )


def validate_project_key(
    value: Any, allowed_projects: Sequence[str], default_project: Optional[str] = None
) -> str:
    project = str(value or default_project or "").strip().upper()
    allowed = tuple(str(item).strip().upper() for item in allowed_projects)
    if not project:
        raise TicketValidationError("Project fehlt.")
    if project not in allowed:
        raise TicketValidationError(
            f"Projekt '{project}' ist nicht freigegeben. Erlaubt: {', '.join(allowed)}."
        )
    return project


def validate_story_points(value: Any) -> None:
    if is_blank(value) or is_clear(value):
        return
    if isinstance(value, bool):
        raise TicketValidationError("Story Points muessen numerisch sein.")
    if isinstance(value, Number):
        return
    try:
        float(str(value).strip().replace(",", "."))
    except ValueError as error:
        raise TicketValidationError("Story Points muessen numerisch sein.") from error


def validate_jira_duration(value: Any, field_name: str) -> None:
    if is_blank(value) or is_clear(value):
        return
    if not _JIRA_DURATION_PATTERN.fullmatch(str(value).strip()):
        raise TicketValidationError(
            f"{field_name} hat kein gueltiges Jira-Zeitformat, z. B. 30m, 2h, 3d oder 4w."
        )


def _required_field_errors(
    row: Mapping[str, Any], issue_type: str, project: str
) -> List[str]:
    schema = get_issue_type_schema(issue_type)
    errors = []
    for field_key in schema.required_fields:
        if field_key == "Project":
            value = project
        elif field_key == "IssueType":
            value = issue_type
        else:
            value = row.get(field_key)
        if is_blank(value) or is_clear(value):
            display_name = FIELD_DEFINITIONS[field_key].display_name
            errors.append(f"{display_name} fehlt fuer {issue_type}.")
    return errors


def _validate_optional_values(row: Mapping[str, Any], errors: List[str]) -> None:
    try:
        validate_story_points(row.get("StoryPoints"))
    except TicketValidationError as error:
        errors.append(str(error))
    for key, display_name in (
        ("OriginalEstimate", "Original Estimate"),
        ("RemainingEstimate", "Remaining Estimate"),
    ):
        try:
            validate_jira_duration(row.get(key), display_name)
        except TicketValidationError as error:
            errors.append(str(error))


def validate_ticket_row(
    row: Mapping[str, Any],
    allowed_projects: Sequence[str],
    default_project: Optional[str] = None,
) -> List[str]:
    """Return user-facing errors without raising, so other rows can continue."""
    errors: List[str] = []
    try:
        action = resolve_action(row)
    except TicketValidationError as error:
        return [str(error)]

    if action is Action.IGNORE:
        return errors

    try:
        project = validate_project_key(
            row.get("Project"), allowed_projects, default_project
        )
    except TicketValidationError as error:
        errors.append(str(error))
        project = str(row.get("Project") or default_project or "").strip().upper()

    raw_issue_type = row.get("IssueType")
    issue_type: Optional[str] = None
    if action is Action.CREATE and is_blank(raw_issue_type):
        errors.append("Issue Type fehlt.")
    elif not is_blank(raw_issue_type):
        try:
            issue_type = normalize_issue_type(raw_issue_type)
        except ValueError as error:
            errors.append(str(error))

    if action is Action.CREATE:
        if issue_type is not None:
            errors.extend(_required_field_errors(row, issue_type, project))
        if not is_blank(row.get("JiraKey")):
            errors.append("Jira Key ist bei CREATE bereits gesetzt.")

    if action is Action.UPDATE:
        jira_key = str(row.get("JiraKey") or "").strip().upper()
        if not jira_key:
            errors.append("Jira Key fehlt fuer UPDATE.")
        elif not _JIRA_KEY_PATTERN.fullmatch(jira_key):
            errors.append("Jira Key hat nicht das erwartete Format, z. B. DAH-123.")
        elif project and jira_key.split("-", 1)[0] != project:
            errors.append("Project darf bei UPDATE nicht vom Jira Key abweichen.")
        if is_clear(row.get("Summary")):
            errors.append("Summary ist ein Pflichtfeld und kann nicht geleert werden.")

    _validate_optional_values(row, errors)
    return errors
