"""Safe CREATE/UPDATE/IGNORE execution with row-level isolation."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from ticketpilot.domain.models import (
    Action,
    CreateResult,
    Preview,
    RelatedOperationResult,
    RowResult,
    Severity,
    SyncStatus,
    TicketSnapshot,
)
from ticketpilot.domain.validation import (
    ValidationContext,
    unavailable_optional_fields,
    validate_command,
)

from .errors import (
    ConcurrencyConflict,
    DefiniteWriteError,
    UncertainWriteError,
    safe_error_message,
)
from .ports import (
    AuditEvent,
    AuditSink,
    JiraGateway,
    MemoryTicketRepository,
    MetadataContextProvider,
    NullAuditSink,
    ProjectPolicy,
    TicketRepository,
)
from .preview import PreviewService, request_fingerprint


class SyncService:
    """Execute previously reviewed previews through all mandatory gates."""

    def __init__(
        self,
        jira: JiraGateway,
        metadata: MetadataContextProvider,
        project_policy: ProjectPolicy,
        *,
        repository: TicketRepository | None = None,
        audit: AuditSink | None = None,
        clock: Callable[[], datetime] | None = None,
        preview_service: PreviewService | None = None,
    ) -> None:
        self._jira = jira
        self._metadata = metadata
        self._policy = project_policy
        self._repository = repository or MemoryTicketRepository()
        self._audit = audit or NullAuditSink()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._preview_service = preview_service or PreviewService(jira, metadata)
        self._execution_lock = RLock()
        self._consumed_preview_ids: set[str] = set()

    def execute(
        self,
        preview: Preview,
        *,
        confirmation_token: str | None = None,
    ) -> RowResult:
        """Execute one row and always return a durable, sanitized result."""

        # This is also a core-level double-click guard. Two UI workers cannot
        # race through the idempotency check and issue duplicate writes.
        with self._execution_lock:
            return self._execute_locked(preview, confirmation_token=confirmation_token)

    def _execute_locked(
        self,
        preview: Preview,
        *,
        confirmation_token: str | None,
    ) -> RowResult:

        command = preview.command
        try:
            if preview.request_fingerprint and preview.request_fingerprint != request_fingerprint(
                command, preview.payload
            ):
                return self._finish(
                    preview,
                    SyncStatus.FAILED,
                    "Preview data changed after review; reload and confirm the new diff.",
                )
            if not preview.is_valid:
                detail = "; ".join(message.message for message in preview.errors)
                return self._finish(preview, SyncStatus.FAILED, detail or "Preview is invalid.")
            if command.action is Action.IGNORE:
                return self._finish(preview, SyncStatus.IGNORED, "Row ignored.")
            # Idempotency is checked before *every* other result is persisted.
            # Otherwise a later dry-run or invalid confirmation could shadow an
            # earlier CREATED/UNCERTAIN result in a last-result repository.
            duplicate = self._duplicate_guard(preview)
            if duplicate is not None:
                return duplicate
            if preview.dry_run:
                return self._finish(
                    preview, SyncStatus.DRY_RUN, "Dry run completed; Jira was not changed."
                )
            if not preview.confirmation_token or confirmation_token != preview.confirmation_token:
                return self._finish(
                    preview,
                    SyncStatus.FAILED,
                    "Explicit confirmation for this preview is required.",
                )
            if not self._policy.is_allowed(command.project):
                return self._finish(
                    preview,
                    SyncStatus.FAILED,
                    f"Project {command.project} is not in the configured allowlist.",
                )
            if preview.preview_id in self._consumed_preview_ids:
                return self._finish(
                    preview,
                    SyncStatus.FAILED,
                    "This confirmed preview was already used; reload and confirm again.",
                )
            # Consume before crossing any remote boundary. A failed or unclear
            # attempt requires a newly loaded preview and fresh confirmation.
            self._consumed_preview_ids.add(preview.preview_id)

            critical_error = self._critical_metadata_error(preview)
            if critical_error:
                return self._finish(preview, SyncStatus.FAILED, critical_error)

            if command.action is Action.CREATE:
                return self._execute_create(preview)
            return self._execute_update(preview)
        except Exception as error:  # row-level isolation boundary
            return self._finish(preview, SyncStatus.FAILED, safe_error_message(error))

    def execute_many(
        self,
        previews: Iterable[Preview],
        *,
        confirmation_tokens: Mapping[str, str] | None = None,
    ) -> tuple[RowResult, ...]:
        tokens = confirmation_tokens or {}
        results: list[RowResult] = []
        for preview in previews:
            try:
                results.append(
                    self.execute(
                        preview,
                        confirmation_token=tokens.get(preview.preview_id),
                    )
                )
            except Exception as error:  # never let one row stop another
                results.append(self._finish(preview, SyncStatus.FAILED, safe_error_message(error)))
        return tuple(results)

    def _critical_metadata_error(self, preview: Preview) -> str | None:
        command = preview.command
        try:
            metadata = self._metadata.get_context(
                command.project,
                command.issue_type,
                force_refresh=True,
            )
        except Exception as error:
            return f"Critical Jira metadata could not be refreshed: {safe_error_message(error)}"
        if metadata.project != command.project or metadata.issue_type is not command.issue_type:
            return "Critical Jira metadata context changed; reload the preview."

        context = ValidationContext(
            available_fields=metadata.available_fields,
            jira_required_fields=metadata.required_fields,
            resolved_field_ids=metadata.field_ids,
        )
        snapshot = preview.snapshot
        messages = validate_command(
            command,
            snapshot=snapshot,
            context=context,
            schema=self._preview_service.schema,
        )
        errors = tuple(message for message in messages if message.severity is Severity.ERROR)
        if errors:
            return "Critical validation failed: " + "; ".join(message.message for message in errors)

        unavailable = unavailable_optional_fields(command, context, self._preview_service.schema)
        if unavailable.intersection(preview.payload):
            return "Jira field availability changed; reload the preview before writing."
        return None

    def _duplicate_guard(self, preview: Preview) -> RowResult | None:
        previous = self._repository.get_last_result(preview.command.row_id)
        if previous is None:
            return None
        if previous.status is SyncStatus.UNCERTAIN and (
            preview.command.action is Action.CREATE
            or previous.request_fingerprint == preview.request_fingerprint
        ):
            return self._finish(
                preview,
                SyncStatus.UNCERTAIN,
                "Previous CREATE outcome is uncertain; reconcile by Jira search before retrying.",
                issue_key=previous.issue_key,
            )
        create_applied = preview.command.action is Action.CREATE and previous.status in (
            SyncStatus.CREATED,
            SyncStatus.DUPLICATE_PREVENTED,
        )
        update_applied = (
            preview.command.action is Action.UPDATE
            and previous.status in (SyncStatus.UPDATED, SyncStatus.DUPLICATE_PREVENTED)
            and previous.request_fingerprint == preview.request_fingerprint
        )
        if create_applied or update_applied:
            return self._finish(
                preview,
                SyncStatus.DUPLICATE_PREVENTED,
                (
                    "CREATE was already applied for this local row; no second POST was sent."
                    if preview.command.action is Action.CREATE
                    else "This UPDATE preview was already applied; no additions were repeated."
                ),
                issue_key=previous.issue_key,
            )
        return None

    def _execute_create(self, preview: Preview) -> RowResult:
        reservation = RowResult(
            row_id=preview.command.row_id,
            status=SyncStatus.UNCERTAIN,
            timestamp=self._clock(),
            message="CREATE attempt reserved before Jira POST.",
            request_fingerprint=preview.request_fingerprint,
            action=Action.CREATE,
        )
        try:
            reserved = self._repository.reserve_create(reservation)
        except Exception as error:
            return self._finish(
                preview,
                SyncStatus.FAILED,
                "CREATE was not sent because its durable attempt guard failed: "
                + safe_error_message(error),
            )
        if not reserved:
            return self._finish(
                preview,
                SyncStatus.UNCERTAIN,
                "A durable CREATE attempt already exists; no second POST was sent.",
            )
        try:
            response = self._jira.create_issue(preview.payload)
            key = _created_key(response, expected_project=preview.command.project)
        except DefiniteWriteError as error:
            return self._finish(preview, SyncStatus.FAILED, safe_error_message(error))
        except Exception as error:
            # Any unexpected CREATE failure is conservatively ambiguous.  It is
            # persisted and never automatically retried.
            return self._finish(
                preview,
                SyncStatus.UNCERTAIN,
                "CREATE may have reached Jira; automatic retry is blocked. "
                + safe_error_message(error),
            )

        try:
            self._repository.save_jira_key(preview.command.row_id, key)
        except Exception as error:
            return self._finish(
                preview,
                SyncStatus.UNCERTAIN,
                "Jira returned a key, but it could not be persisted locally: "
                + safe_error_message(error),
                issue_key=key,
            )

        related = self._execute_related(key, preview)
        failures = sum(not item.succeeded for item in related)
        message = (
            "Ticket created."
            if not related
            else "Ticket created; all additions completed."
            if not failures
            else f"Ticket created; {failures} attachment/link operation(s) failed independently."
        )
        return self._finish(
            preview,
            SyncStatus.CREATED,
            message,
            issue_key=key,
            related=related,
        )

    def _execute_update(self, preview: Preview) -> RowResult:
        snapshot = preview.snapshot
        if snapshot is None or not preview.command.jira_key:
            return self._finish(preview, SyncStatus.FAILED, "UPDATE preview has no Jira snapshot.")
        try:
            current = self._jira.fetch_issue(preview.command.jira_key)
        except Exception as error:
            return self._finish(preview, SyncStatus.FAILED, safe_error_message(error))
        if current.updated != snapshot.updated:
            return self._finish(
                preview,
                SyncStatus.CONFLICT,
                "Jira changed after preview; reload and review the diff again.",
                issue_key=current.key,
            )
        if (
            current.key != snapshot.key
            or current.project != snapshot.project
            or current.issue_type is not snapshot.issue_type
            or current.reporter != snapshot.reporter
            or current.status != snapshot.status
        ):
            return self._finish(
                preview,
                SyncStatus.CONFLICT,
                "A protected Jira identity field changed; reload before updating.",
                issue_key=current.key,
            )

        if preview.payload:
            try:
                self._jira.update_issue(
                    current.key,
                    preview.payload,
                    expected_updated=snapshot.updated,
                )
            except ConcurrencyConflict as error:
                return self._finish(
                    preview,
                    SyncStatus.CONFLICT,
                    safe_error_message(error),
                    issue_key=current.key,
                )
            except UncertainWriteError as error:
                return self._finish(
                    preview,
                    SyncStatus.UNCERTAIN,
                    safe_error_message(error),
                    issue_key=current.key,
                )
            except DefiniteWriteError as error:
                return self._finish(
                    preview,
                    SyncStatus.FAILED,
                    safe_error_message(error),
                    issue_key=current.key,
                )
            except Exception as error:
                return self._finish(
                    preview,
                    SyncStatus.UNCERTAIN,
                    "UPDATE may have reached Jira; automatic retry is blocked. "
                    + safe_error_message(error),
                    issue_key=current.key,
                )

        related = self._execute_related(current.key, preview)
        failures = sum(not item.succeeded for item in related)
        message = (
            "Ticket update completed."
            if not related
            else "Ticket update and all additions completed."
            if not failures
            else f"Ticket updated; {failures} attachment/link operation(s) failed independently."
        )
        return self._finish(
            preview,
            SyncStatus.UPDATED,
            message,
            issue_key=current.key,
            related=related,
        )

    def _execute_related(
        self, issue_key: str, preview: Preview
    ) -> tuple[RelatedOperationResult, ...]:
        results: list[RelatedOperationResult] = []
        for attachment in preview.command.attachments:
            try:
                self._jira.upload_attachment(issue_key, attachment)
                results.append(
                    RelatedOperationResult("attachment", attachment.reference, True, "Uploaded.")
                )
            except DefiniteWriteError as error:
                results.append(
                    RelatedOperationResult(
                        "attachment",
                        attachment.reference,
                        False,
                        safe_error_message(error),
                    )
                )
            except Exception as error:
                results.append(
                    RelatedOperationResult(
                        "attachment",
                        attachment.reference,
                        False,
                        safe_error_message(error),
                        uncertain=True,
                    )
                )
        for link in preview.command.links:
            reference = f"{link.link_type_id}:{link.target_key}:{link.direction.value}"
            try:
                self._jira.add_issue_link(issue_key, link)
                results.append(RelatedOperationResult("link", reference, True, "Added."))
            except DefiniteWriteError as error:
                results.append(
                    RelatedOperationResult("link", reference, False, safe_error_message(error))
                )
            except Exception as error:
                results.append(
                    RelatedOperationResult(
                        "link",
                        reference,
                        False,
                        safe_error_message(error),
                        uncertain=True,
                    )
                )
        return tuple(results)

    def _finish(
        self,
        preview: Preview,
        status: SyncStatus,
        message: str,
        *,
        issue_key: str | None = None,
        related: tuple[RelatedOperationResult, ...] = (),
    ) -> RowResult:
        safe_message = safe_error_message(message)
        result = RowResult(
            row_id=preview.command.row_id,
            status=status,
            timestamp=self._clock(),
            issue_key=issue_key,
            message=safe_message,
            request_fingerprint=preview.request_fingerprint,
            related=related,
            action=preview.command.action,
        )
        # A result-store failure must not trigger a second remote write.
        with suppress(Exception):
            self._repository.save_result(result)
        with suppress(Exception):
            self._audit.record(
                AuditEvent(
                    occurred_at=result.timestamp,
                    event_type=preview.command.action.value,
                    row_id=result.row_id,
                    outcome=result.status.value,
                    issue_key=result.issue_key,
                    detail=result.message,
                    dry_run=preview.dry_run,
                )
            )
        return result


def _created_key(response: Any, *, expected_project: str) -> str:
    key: object
    if isinstance(response, CreateResult):
        key = response.issue_key
    elif isinstance(response, TicketSnapshot):
        key = response.key
    elif isinstance(response, str):
        key = response
    elif isinstance(response, Mapping):
        key = response.get("key") or response.get("issue_key")
    else:
        key = None
    project = expected_project.strip().upper()
    expected_key = re.compile(rf"^{re.escape(project)}-\d+$")
    if not isinstance(key, str) or expected_key.fullmatch(key) is None:
        raise UncertainWriteError(
            "Jira CREATE response did not contain an unambiguous key for the target project."
        )
    return key
