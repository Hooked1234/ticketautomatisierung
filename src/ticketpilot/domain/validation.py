"""Business validation independent of all adapters and user interfaces."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from numbers import Real
from typing import Any

from .models import (
    Action,
    AssigneeMode,
    AssigneeSelection,
    IssueType,
    Severity,
    SprintState,
    TicketCommand,
    TicketSnapshot,
    ValidationMessage,
    is_blank,
    is_clear,
)
from .schema import (
    ISSUE_TYPE,
    JIRA_KEY,
    PROJECT,
    REPORTER,
    SPRINT,
    STATUS,
    SUMMARY,
    TicketSchema,
    ValueKind,
    default_ticket_schema,
)

_TIME_TRACKING = re.compile(r"^(?:\d+[wdhm])(?:\s+\d+[wdhm])*$", re.IGNORECASE)
_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """Context resolved from Jira create/edit metadata.

    ``available_fields=None`` means that metadata has not been supplied yet;
    an empty set means Jira explicitly exposes no optional fields.  Jira may
    add requirements to the central schema but cannot remove its requirements.
    """

    available_fields: frozenset[str] | None = None
    jira_required_fields: frozenset[str] = frozenset()
    resolved_field_ids: Mapping[str, str] | None = None


def command_values(command: TicketCommand) -> dict[str, Any]:
    values: dict[str, Any] = {
        PROJECT: command.project,
        ISSUE_TYPE: command.issue_type.value,
        SUMMARY: command.summary,
    }
    values.update(command.fields)
    if command.sprint is not None:
        values[SPRINT] = command.sprint
    return values


def effective_values(
    command: TicketCommand,
    snapshot: TicketSnapshot | None,
) -> dict[str, Any]:
    """Apply CREATE/UPDATE empty and CLEAR semantics without mutating inputs."""

    if command.action is Action.UPDATE and snapshot is not None:
        result = dict(snapshot.fields)
        result.setdefault(PROJECT, snapshot.project)
        result.setdefault(ISSUE_TYPE, snapshot.issue_type.value)
        result.setdefault(REPORTER, snapshot.reporter)
        result.setdefault(STATUS, snapshot.status)
        result.setdefault(JIRA_KEY, snapshot.key)
        incoming = command_values(command)
        for name, value in incoming.items():
            if is_blank(value):
                continue
            result[name] = None if is_clear(value) else value
        return result

    return {
        name: value
        for name, value in command_values(command).items()
        if not is_blank(value) and not is_clear(value)
    }


def validate_command(
    command: TicketCommand,
    *,
    snapshot: TicketSnapshot | None = None,
    context: ValidationContext | None = None,
    schema: TicketSchema | None = None,
) -> tuple[ValidationMessage, ...]:
    """Validate a command against central rules and optional Jira metadata."""

    schema = schema or default_ticket_schema()
    context = context or ValidationContext()
    messages: list[ValidationMessage] = []

    if not command.row_id.strip():
        _error(messages, "ROW_ID_REQUIRED", "A stable local row ID is required.", "row_id")

    if command.action is Action.IGNORE:
        return tuple(messages)

    if not command.project.strip():
        _error(messages, "PROJECT_REQUIRED", "Project is required.", PROJECT)

    if command.action is Action.CREATE and command.jira_key:
        _error(
            messages,
            "CREATE_HAS_JIRA_KEY",
            "CREATE cannot target an existing Jira key.",
            JIRA_KEY,
        )

    if command.action is Action.UPDATE:
        if not command.jira_key:
            _error(messages, "JIRA_KEY_REQUIRED", "UPDATE requires a Jira key.", JIRA_KEY)
        if snapshot is None:
            _error(
                messages,
                "SNAPSHOT_REQUIRED",
                "UPDATE requires the current Jira state.",
                JIRA_KEY,
            )
        else:
            _validate_update_identity(command, snapshot, messages)

    incoming = command_values(command)
    _validate_explicit_fields(command, incoming, schema, context, messages)
    _validate_sprint(command, messages)
    _validate_attachments_and_links(command, messages)

    effective = effective_values(command, snapshot)
    required = schema.required_fields(command.issue_type) | context.jira_required_fields
    for field_name in sorted(required):
        value = effective.get(field_name)
        if is_blank(value) or value is None or _empty_collection(value):
            _error(
                messages,
                "REQUIRED_FIELD",
                f"{field_name} is required for {command.issue_type.value}.",
                field_name,
            )

    return _deduplicate(messages)


def _validate_update_identity(
    command: TicketCommand,
    snapshot: TicketSnapshot,
    messages: list[ValidationMessage],
) -> None:
    if command.jira_key != snapshot.key:
        _error(messages, "JIRA_KEY_IMMUTABLE", "Jira key cannot be changed.", JIRA_KEY)
    if command.project != snapshot.project:
        _error(messages, "PROJECT_IMMUTABLE", "Project cannot be changed.", PROJECT)
    if command.issue_type is not snapshot.issue_type:
        _error(
            messages,
            "ISSUE_TYPE_IMMUTABLE",
            "Issue type cannot be changed.",
            ISSUE_TYPE,
        )

    protected = {
        PROJECT: snapshot.project,
        ISSUE_TYPE: snapshot.issue_type.value,
        REPORTER: snapshot.reporter,
        JIRA_KEY: snapshot.key,
        STATUS: snapshot.status,
    }
    for name in protected:
        if name not in command.fields:
            continue
        value = command.fields[name]
        if is_blank(value):
            continue
        _error(
            messages,
            f"{name.upper()}_IMMUTABLE",
            f"{name} is read-only for UPDATE.",
            name,
        )


def _validate_explicit_fields(
    command: TicketCommand,
    incoming: Mapping[str, Any],
    schema: TicketSchema,
    context: ValidationContext,
    messages: list[ValidationMessage],
) -> None:
    identity = {PROJECT, ISSUE_TYPE}
    for field_name, value in incoming.items():
        if field_name in identity:
            continue
        if field_name in {REPORTER, JIRA_KEY, STATUS}:
            if not is_blank(value):
                _error(
                    messages,
                    "IMMUTABLE_FIELD",
                    f"{field_name} is managed by Jira and cannot be written.",
                    field_name,
                )
            continue

        rule = schema.rule(field_name)
        if rule is None:
            if not is_blank(value):
                _error(
                    messages,
                    "UNKNOWN_FIELD",
                    f"Unsupported field: {field_name}.",
                    field_name,
                )
            continue
        if is_blank(value):
            continue
        if not rule.allowed_for(command.issue_type):
            _error(
                messages,
                "FIELD_NOT_ALLOWED",
                f"{field_name} is not available for {command.issue_type.value}.",
                field_name,
            )
            continue
        if is_clear(value):
            if command.action is Action.CREATE:
                _error(
                    messages,
                    "CLEAR_ONLY_ON_UPDATE",
                    "<CLEAR> is only valid for UPDATE.",
                    field_name,
                )
            elif not rule.clearable or rule.required_for_type(command.issue_type):
                _error(
                    messages,
                    "REQUIRED_FIELD_CANNOT_CLEAR",
                    f"{field_name} cannot be cleared.",
                    field_name,
                )
            continue

        if (
            context.available_fields is not None
            and rule.metadata_controlled
            and field_name not in context.available_fields
        ):
            severity = (
                Severity.ERROR if rule.required_for_type(command.issue_type) else Severity.WARNING
            )
            messages.append(
                ValidationMessage(
                    code="FIELD_UNAVAILABLE",
                    message=(
                        f"{field_name} is unavailable in the current Jira context "
                        "and will not be sent."
                    ),
                    field=field_name,
                    severity=severity,
                )
            )
            continue

        if (
            rule.metadata_controlled
            and context.resolved_field_ids is not None
            and not context.resolved_field_ids.get(field_name)
        ):
            _error(
                messages,
                "FIELD_ID_UNRESOLVED",
                f"{field_name} has no unambiguous Jira field ID for this context.",
                field_name,
            )
            continue

        _validate_value(field_name, value, rule.value_kind, messages)


def _validate_value(
    field_name: str,
    value: Any,
    kind: ValueKind,
    messages: list[ValidationMessage],
) -> None:
    if kind is ValueKind.TEXT:
        if not isinstance(value, str) or not value.strip():
            _error(messages, "INVALID_TEXT", f"{field_name} must contain text.", field_name)
        return
    if kind in (ValueKind.OPTION, ValueKind.ISSUE):
        if not isinstance(value, str) or not value.strip():
            _error(
                messages,
                "INVALID_SELECTION",
                f"{field_name} must be a resolved selection.",
                field_name,
            )
        return
    if kind is ValueKind.USER:
        if isinstance(value, AssigneeSelection):
            if value.mode is AssigneeMode.USER and not (value.user_id and value.user_id.strip()):
                _error(
                    messages,
                    "ASSIGNEE_USER_REQUIRED",
                    "A resolved assignable user is required.",
                    field_name,
                )
            if value.mode is not AssigneeMode.USER and value.user_id is not None:
                _error(
                    messages,
                    "ASSIGNEE_USER_FORBIDDEN",
                    "This assignee mode cannot carry a user ID.",
                    field_name,
                )
        elif not isinstance(value, str) or not value.strip():
            _error(
                messages,
                "INVALID_ASSIGNEE",
                f"{field_name} must be Unassigned, self, or a resolved user.",
                field_name,
            )
        return
    if kind is ValueKind.NUMBER:
        if isinstance(value, bool) or not isinstance(value, Real):
            _error(messages, "INVALID_NUMBER", f"{field_name} must be numeric.", field_name)
        return
    if kind is ValueKind.DATE:
        if not isinstance(value, date):
            _error(messages, "INVALID_DATE", f"{field_name} must be a date value.", field_name)
        return
    if kind is ValueKind.BOOLEAN:
        if not isinstance(value, bool):
            _error(messages, "INVALID_BOOLEAN", f"{field_name} must be Yes/No.", field_name)
        return
    if kind is ValueKind.TIME_ESTIMATE:
        if not isinstance(value, str) or not _TIME_TRACKING.fullmatch(value):
            _error(
                messages,
                "INVALID_TIME_ESTIMATE",
                f"{field_name} must use Jira time syntax such as 30m, 2h, 3d, or 4w.",
                field_name,
            )
        return
    if kind in (ValueKind.MULTI_OPTION, ValueKind.MULTI_USER):
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
            _error(messages, "INVALID_MULTI_VALUE", f"{field_name} must be a list.", field_name)
        elif any(is_blank(item) for item in value):
            _error(
                messages,
                "EMPTY_MULTI_VALUE",
                f"{field_name} contains an empty selection.",
                field_name,
            )
        elif any(not isinstance(item, str) or not item.strip() for item in value):
            _error(
                messages,
                "INVALID_MULTI_SELECTION",
                f"{field_name} contains an unresolved selection.",
                field_name,
            )
        return
    if kind is ValueKind.SPRINT and not hasattr(value, "sprint_id"):
        _error(messages, "INVALID_SPRINT", f"{field_name} must be a resolved sprint.", field_name)


def _validate_sprint(
    command: TicketCommand,
    messages: list[ValidationMessage],
) -> None:
    sprint = command.sprint
    if sprint is None:
        return
    if command.issue_type is IssueType.EPIC:
        _error(messages, "EPIC_SPRINT_FORBIDDEN", "Epics cannot be assigned to a sprint.", SPRINT)
    if sprint.state not in (SprintState.ACTIVE, SprintState.FUTURE):
        _error(
            messages,
            "CLOSED_SPRINT_FORBIDDEN",
            "Only active or future sprints can be newly assigned.",
            SPRINT,
        )
    if not sprint.sprint_id.strip() or not sprint.name.strip():
        _error(messages, "INVALID_SPRINT", "Sprint ID and name are required.", SPRINT)


def _validate_attachments_and_links(
    command: TicketCommand,
    messages: list[ValidationMessage],
) -> None:
    seen_attachments: set[str] = set()
    for attachment in command.attachments:
        reference = attachment.reference.strip()
        if not reference:
            _error(
                messages, "INVALID_ATTACHMENT", "Attachment reference is required.", "attachments"
            )
        elif reference in seen_attachments:
            _error(
                messages,
                "DUPLICATE_ATTACHMENT",
                "Attachment is listed more than once.",
                "attachments",
            )
        seen_attachments.add(reference)

    seen_links: set[tuple[str, str, object]] = set()
    for link in command.links:
        identity = (link.link_type_id, link.target_key, link.direction)
        if not link.link_type_id.strip():
            _error(
                messages,
                "LINK_TYPE_REQUIRED",
                "A metadata-resolved link type is required.",
                "links",
            )
        if not _JIRA_KEY.fullmatch(link.target_key):
            _error(messages, "INVALID_LINK_TARGET", "Link target must be a Jira key.", "links")
        if identity in seen_links:
            _error(messages, "DUPLICATE_LINK", "Issue link is listed more than once.", "links")
        seen_links.add(identity)


def unavailable_optional_fields(
    command: TicketCommand,
    context: ValidationContext,
    schema: TicketSchema,
) -> frozenset[str]:
    """Fields that metadata says must be omitted from the outgoing payload."""

    if context.available_fields is None:
        return frozenset()
    unavailable: set[str] = set()
    for name, value in command_values(command).items():
        rule = schema.rule(name)
        if (
            rule is not None
            and rule.metadata_controlled
            and name not in context.available_fields
            and not is_blank(value)
            and not rule.required_for_type(command.issue_type)
        ):
            unavailable.add(name)
    return frozenset(unavailable)


def _empty_collection(value: Any) -> bool:
    return isinstance(value, (list, tuple, set, frozenset)) and not value


def _error(
    messages: list[ValidationMessage],
    code: str,
    message: str,
    field: str | None,
) -> None:
    messages.append(ValidationMessage(code=code, message=message, field=field))


def _deduplicate(messages: Iterable[ValidationMessage]) -> tuple[ValidationMessage, ...]:
    result: list[ValidationMessage] = []
    seen: set[tuple[str, str | None, Severity]] = set()
    for message in messages:
        key = (message.code, message.field, message.severity)
        if key not in seen:
            result.append(message)
            seen.add(key)
    return tuple(result)
