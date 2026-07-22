"""Preview and field-level diff use case."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, cast

from ticketpilot.domain.models import (
    Action,
    AssigneeMode,
    AssigneeSelection,
    ChangeKind,
    FieldChange,
    Preview,
    Severity,
    SprintRef,
    TicketCommand,
    TicketSnapshot,
    ValidationMessage,
    is_blank,
    is_clear,
)
from ticketpilot.domain.schema import (
    ISSUE_TYPE,
    JIRA_KEY,
    PROJECT,
    REPORTER,
    STATUS,
    TicketSchema,
    default_ticket_schema,
)
from ticketpilot.domain.validation import (
    ValidationContext,
    command_values,
    unavailable_optional_fields,
    validate_command,
)

from .errors import safe_error_message
from .ports import JiraReadGateway, MetadataContext, MetadataContextProvider


class PreviewService:
    """Create immutable previews without performing a Jira write."""

    def __init__(
        self,
        jira: JiraReadGateway,
        metadata: MetadataContextProvider,
        *,
        schema: TicketSchema | None = None,
    ) -> None:
        self._jira = jira
        self._metadata = metadata
        self._schema = schema or default_ticket_schema()

    @property
    def schema(self) -> TicketSchema:
        return self._schema

    def preview(self, command: TicketCommand, *, dry_run: bool = True) -> Preview:
        if command.action is Action.IGNORE:
            return self._assemble(
                command,
                dry_run=dry_run,
                payload={},
                changes=(),
                snapshot=None,
                messages=validate_command(command, schema=self._schema),
            )

        snapshot: TicketSnapshot | None = None
        try:
            if command.action is Action.UPDATE and command.jira_key:
                snapshot = self._jira.fetch_issue(command.jira_key)
            metadata = self._metadata.get_context(
                command.project,
                command.issue_type,
                force_refresh=False,
            )
        except Exception as error:
            return self._invalid_preview(
                command,
                dry_run,
                code="PREVIEW_LOAD_FAILED",
                message=safe_error_message(error),
                snapshot=snapshot,
            )

        validation_context = _validation_context(metadata)
        messages = list(
            validate_command(
                command,
                snapshot=snapshot,
                context=validation_context,
                schema=self._schema,
            )
        )
        omitted = unavailable_optional_fields(command, validation_context, self._schema)

        if command.action is Action.CREATE:
            payload, changes = self._create_payload(command, omitted)
        else:
            payload, changes = self._update_payload(command, snapshot, omitted)
            if not changes and not command.attachments and not command.links:
                messages.append(
                    ValidationMessage(
                        code="NO_CHANGES",
                        message="No Jira changes were selected.",
                        severity=Severity.WARNING,
                    )
                )

        return self._assemble(
            command,
            dry_run=dry_run,
            payload=payload,
            changes=changes,
            snapshot=snapshot,
            messages=messages,
        )

    def preview_many(
        self,
        commands: Iterable[TicketCommand],
        *,
        dry_run: bool = True,
    ) -> tuple[Preview, ...]:
        """Preview every row independently, even if an unexpected row fails."""

        previews: list[Preview] = []
        for command in commands:
            try:
                previews.append(self.preview(command, dry_run=dry_run))
            except Exception as error:  # last row-level isolation boundary
                previews.append(
                    self._invalid_preview(
                        command,
                        dry_run,
                        code="ROW_PREVIEW_FAILED",
                        message=safe_error_message(error),
                    )
                )
        return tuple(previews)

    def _create_payload(
        self,
        command: TicketCommand,
        omitted: frozenset[str],
    ) -> tuple[dict[str, Any], tuple[FieldChange, ...]]:
        values = command_values(command)
        payload: dict[str, Any] = {}
        for name, value in values.items():
            if name in {REPORTER, JIRA_KEY, STATUS} or name in omitted:
                continue
            if is_blank(value) or is_clear(value):
                continue
            payload[name] = _outgoing_value(value)
        changes = tuple(
            FieldChange(name, None, value, ChangeKind.SET) for name, value in payload.items()
        )
        return payload, changes

    def _update_payload(
        self,
        command: TicketCommand,
        snapshot: TicketSnapshot | None,
        omitted: frozenset[str],
    ) -> tuple[dict[str, Any], tuple[FieldChange, ...]]:
        if snapshot is None:
            return {}, ()
        protected = {PROJECT, ISSUE_TYPE, REPORTER, JIRA_KEY, STATUS}
        incoming = command_values(command)
        payload: dict[str, Any] = {}
        changes: list[FieldChange] = []
        for name, value in incoming.items():
            if name in protected or name in omitted or is_blank(value):
                continue
            before = snapshot.fields.get(name)
            if is_clear(value):
                if before is None or is_blank(before):
                    continue
                payload[name] = None
                changes.append(FieldChange(name, before, None, ChangeKind.CLEAR))
                continue
            after = _outgoing_value(value)
            if _equivalent(before, after):
                continue
            payload[name] = after
            changes.append(FieldChange(name, before, after, ChangeKind.SET))
        return payload, tuple(changes)

    def _assemble(
        self,
        command: TicketCommand,
        *,
        dry_run: bool,
        payload: Mapping[str, Any],
        changes: tuple[FieldChange, ...],
        snapshot: TicketSnapshot | None,
        messages: Iterable[ValidationMessage],
    ) -> Preview:
        message_tuple = tuple(messages)
        valid = not any(message.severity is Severity.ERROR for message in message_tuple)
        fingerprint = request_fingerprint(command, payload)
        confirmation = (
            secrets.token_urlsafe(24)
            if valid and not dry_run and command.action in (Action.CREATE, Action.UPDATE)
            else None
        )
        return Preview(
            preview_id=str(uuid.uuid4()),
            command=command,
            dry_run=dry_run,
            payload=dict(payload),
            changes=changes,
            snapshot=snapshot,
            messages=message_tuple,
            confirmation_token=confirmation,
            request_fingerprint=fingerprint,
        )

    def _invalid_preview(
        self,
        command: TicketCommand,
        dry_run: bool,
        *,
        code: str,
        message: str,
        snapshot: TicketSnapshot | None = None,
    ) -> Preview:
        return self._assemble(
            command,
            dry_run=dry_run,
            payload={},
            changes=(),
            snapshot=snapshot,
            messages=(ValidationMessage(code=code, message=message),),
        )


def request_fingerprint(command: TicketCommand, payload: Mapping[str, Any]) -> str:
    """Stable non-secret hash used to suppress accidental repeated CREATEs."""

    material = {
        "row_id": command.row_id,
        "action": command.action.value,
        "project": command.project,
        "issue_type": command.issue_type.value,
        "jira_key": command.jira_key,
        "payload": payload,
        "attachments": command.attachments,
        "links": command.links,
    }
    encoded = json.dumps(
        _canonical(material),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validation_context(metadata: MetadataContext) -> ValidationContext:
    return ValidationContext(
        available_fields=metadata.available_fields,
        jira_required_fields=metadata.required_fields,
        resolved_field_ids=metadata.field_ids,
    )


def _outgoing_value(value: Any) -> Any:
    if isinstance(value, SprintRef):
        return value.sprint_id
    if isinstance(value, AssigneeSelection):
        if value.mode is AssigneeMode.UNASSIGNED:
            return None
        if value.mode is AssigneeMode.SELF:
            return {"mode": AssigneeMode.SELF.value}
        return {"mode": AssigneeMode.USER.value, "user_id": value.user_id}
    return value


def _equivalent(before: Any, after: Any) -> bool:
    before = _comparison_value(before)
    after = _comparison_value(after)
    if isinstance(before, (list, tuple, set, frozenset)) and isinstance(
        after, (list, tuple, set, frozenset)
    ):
        return list(before) == list(after)
    return bool(before == after)


def _comparison_value(value: Any) -> Any:
    """Normalize typed assignee choices against Jira-style read identities."""

    if isinstance(value, AssigneeSelection):
        value = _outgoing_value(value)
    if isinstance(value, Mapping):
        mode = str(value.get("mode", "")).upper()
        if mode == AssigneeMode.USER.value:
            return value.get("user_id")
        if mode == AssigneeMode.UNASSIGNED.value:
            return None
        if mode == AssigneeMode.SELF.value:
            return "@me"
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return _canonical(asdict(cast(Any, value)))
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=repr)
    return value
