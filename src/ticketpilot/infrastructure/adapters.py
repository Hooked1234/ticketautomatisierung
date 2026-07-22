"""Small SQLite adapters that directly implement application-layer ports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ticketpilot.application.ports import AuditEvent
from ticketpilot.domain.models import Action, RelatedOperationResult, RowResult, SyncStatus

from .persistence import SQLiteStore


class SQLiteTicketRepository:
    """Durable result/idempotency adapter backed by :class:`SQLiteStore`."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def get_last_result(self, row_id: str) -> RowResult | None:
        rows = self.store.list_sync_results(local_id=row_id, limit=1)
        attempt = self.store.get_create_attempt(row_id)
        if not rows:
            return _reservation_result(attempt)
        row = rows[0]
        if attempt is not None and attempt["reserved_at"] > row["created_at"]:
            return _reservation_result(attempt)
        details = row["details"] if isinstance(row["details"], dict) else {}
        related_raw = details.get("related", ())
        related: list[RelatedOperationResult] = []
        if isinstance(related_raw, list):
            for item in related_raw:
                if not isinstance(item, dict):
                    continue
                related.append(
                    RelatedOperationResult(
                        kind=str(item.get("kind", "")),
                        reference=str(item.get("reference", "")),
                        succeeded=bool(item.get("succeeded", False)),
                        message=str(item.get("message", "")),
                        uncertain=bool(item.get("uncertain", False)),
                    )
                )
        timestamp_raw = details.get("result_timestamp")
        timestamp = (
            datetime.fromisoformat(str(timestamp_raw)) if timestamp_raw else row["created_at"]
        )
        action_raw = details.get("action")
        try:
            action = Action(str(action_raw)) if action_raw else None
        except ValueError:
            action = None
        return RowResult(
            row_id=str(row["local_id"]),
            status=SyncStatus(str(row["outcome"])),
            timestamp=timestamp,
            issue_key=row["jira_key"],
            message=str(row["message"]),
            request_fingerprint=str(details.get("request_fingerprint", "")),
            related=tuple(related),
            action=action,
        )

    def save_jira_key(self, row_id: str, issue_key: str) -> None:
        existing = self.store.get_ticket(row_id)
        data = existing["data"] if existing else {}
        action = str(existing["action"]) if existing else "CREATE"
        status = str(existing["status"]) if existing else "CREATED"
        self.store.upsert_ticket(
            row_id,
            data,
            jira_key=issue_key,
            action=action,
            status=status,
        )

    def save_result(self, result: RowResult) -> None:
        existing = self.store.get_ticket(result.row_id)
        action = _result_action(result, existing)
        details: dict[str, Any] = {
            "result_timestamp": result.timestamp.isoformat(),
            "request_fingerprint": result.request_fingerprint,
            "related": [
                {
                    "kind": item.kind,
                    "reference": item.reference,
                    "succeeded": item.succeeded,
                    "message": item.message,
                    "uncertain": item.uncertain,
                }
                for item in result.related
            ],
        }
        if result.action is not None:
            details["action"] = result.action.value
        self.store.add_sync_result(
            local_id=result.row_id,
            jira_key=result.issue_key,
            action=action,
            outcome=result.status.value,
            message=result.message,
            details=details,
        )
        if existing is not None or result.issue_key is not None:
            self.store.upsert_ticket(
                result.row_id,
                existing["data"] if existing else {},
                jira_key=result.issue_key or (existing["jira_key"] if existing else None),
                action=action,
                status=result.status.value,
            )

    def reserve_create(self, result: RowResult) -> bool:
        return self.store.reserve_create_attempt(
            local_id=result.row_id,
            request_fingerprint=result.request_fingerprint,
            message=result.message,
            reserved_at=result.timestamp,
        )


class SQLiteAuditSink:
    """Sanitized application audit port; raw ticket payloads are never accepted."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def record(self, event: AuditEvent) -> None:
        self.store.append_audit(
            event.event_type,
            entity_type="ticket-row",
            entity_id=event.row_id,
            details={
                "occurred_at": event.occurred_at.isoformat(),
                "outcome": event.outcome,
                "issue_key": event.issue_key,
                "detail": event.detail,
                "dry_run": event.dry_run,
            },
        )


def _action_from_status(status: SyncStatus) -> str:
    if status is SyncStatus.UPDATED or status is SyncStatus.CONFLICT:
        return "UPDATE"
    if status is SyncStatus.IGNORED:
        return "IGNORE"
    return "CREATE"


def _result_action(result: RowResult, existing: dict[str, Any] | None) -> str:
    if result.action is not None:
        return result.action.value
    if existing is not None and existing.get("action"):
        return str(existing["action"])
    return _action_from_status(result.status)


def _reservation_result(attempt: dict[str, Any] | None) -> RowResult | None:
    if attempt is None:
        return None
    return RowResult(
        row_id=str(attempt["local_id"]),
        status=SyncStatus.UNCERTAIN,
        timestamp=attempt["reserved_at"],
        message=str(attempt["message"]),
        request_fingerprint=str(attempt["request_fingerprint"]),
        action=Action.CREATE,
    )


__all__ = ["SQLiteAuditSink", "SQLiteTicketRepository"]
