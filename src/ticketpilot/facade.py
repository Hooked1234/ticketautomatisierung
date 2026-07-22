"""Composition-facing facade used by the PySide6 presentation layer.

The facade translates UI records into the pure domain model.  It intentionally
contains no Qt types and performs no network access: the current product uses
the deterministic :class:`LocalDemoGateway` until a real Jira adapter is added.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ticketpilot.application import (
    CommentService,
    PreviewService,
    ReportingService,
    StaticProjectPolicy,
    SyncService,
)
from ticketpilot.domain.models import (
    Action,
    AssigneeMode,
    AssigneeSelection,
    Attachment,
    IssueLink,
    IssueType,
    LinkDirection,
    Preview,
    Severity,
    SprintRef,
    SprintState,
    SyncStatus,
    TicketCommand,
    TicketSnapshot,
)
from ticketpilot.domain.models import (
    ValidationMessage as DomainValidationMessage,
)
from ticketpilot.domain.reporting import ReportFilter
from ticketpilot.domain.schema import ValueKind, default_ticket_schema
from ticketpilot.infrastructure.adapters import SQLiteAuditSink, SQLiteTicketRepository
from ticketpilot.infrastructure.config import AppConfig, ConfigError
from ticketpilot.infrastructure.credentials import CredentialStore, create_credential_store
from ticketpilot.infrastructure.gateways import LocalDemoGateway
from ticketpilot.infrastructure.metadata_cache import (
    DEFAULT_SMALL_METADATA_KINDS,
    CachingMetadataProvider,
)
from ticketpilot.infrastructure.persistence import SQLiteStore
from ticketpilot.infrastructure.security import register_secret, safe_error_message
from ticketpilot.ui import contracts as ui

DEFAULT_JIRA_URL = "https://jira.dfd-hamburg.de"
DEFAULT_VISIBLE_COLUMNS = (
    "action",
    "project",
    "issue_type",
    "summary",
    "jira_key",
    "result",
)

_FIELD_LABELS = {
    "project": "Projekt",
    "issue_type": "Vorgangstyp",
    "summary": "Zusammenfassung",
    "epic_name": "Epic Name",
    "components": "Komponenten",
    "description": "Beschreibung",
    "priority": "Priorität",
    "products_services": "Products and Services",
    "account": "Account",
    "labels": "Labels",
    "start_date": "Startdatum",
    "due_date": "Fälligkeitsdatum",
    "story_points": "Story Points",
    "assignee": "Zuständig",
    "teams": "Mitarbeitende Teams",
    "participants": "Beteiligte",
    "epic_link": "Epic / Parent",
    "parent_link": "Parent Link",
    "original_estimate": "Ursprüngliche Schätzung",
    "remaining_estimate": "Restaufwand",
    "impediment": "Markiert / Impediment",
    "fix_versions": "Fix Versions",
    "sprint": "Sprint",
    "reporter": "Reporter",
    "jira_key": "Jira Key",
    "status": "Jira-Status",
    "jira_status": "Jira-Status",
}

_STATUS_LABELS = {
    SyncStatus.READY.value: "Bereit",
    SyncStatus.IGNORED.value: "Ignoriert",
    SyncStatus.DRY_RUN.value: "Dry Run erfolgreich",
    SyncStatus.CREATED.value: "Erstellt",
    SyncStatus.UPDATED.value: "Aktualisiert",
    SyncStatus.FAILED.value: "Fehler",
    SyncStatus.CONFLICT.value: "Konflikt",
    SyncStatus.UNCERTAIN.value: "Unklarer Zustand",
    SyncStatus.DUPLICATE_PREVENTED.value: "Doppelte Erstellung verhindert",
    "DRAFT": "Entwurf",
}


class OfflineTicketPilotFacade:
    """Thread-safe facade for the local, synthetic product mode."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        data_directory: Path,
        credentials: CredentialStore | None = None,
        config: AppConfig | None = None,
    ) -> None:
        self._store = store
        self._data_directory = Path(data_directory)
        self._credentials = credentials or create_credential_store()
        self._lock = threading.RLock()
        self._previews: dict[str, Preview] = {}
        self._preview_drafts: dict[str, ui.TicketDraft] = {}
        self._executed_results: dict[str, ui.RowResult] = {}
        self._session_token: str | None = None
        self._schema = default_ticket_schema()
        self._config = config or self._load_config()
        self._store.set_cache_ttl(timedelta(hours=self._config.cache_ttl_hours))
        self._compose_services()

    def _load_config(self) -> AppConfig:
        values = {
            "dry_run": self._store.get_setting("dry_run", True),
            "jira_url": self._store.get_setting("jira_url", DEFAULT_JIRA_URL),
            "project_allowlist": self._store.get_setting("project_allowlist", ["DAH"]),
            "cache_ttl_hours": self._store.get_setting("cache_ttl_hours", 24),
            "demo_mode": True,
            "selected_project": self._store.get_setting("selected_project", "DAH"),
        }
        try:
            return AppConfig.from_mapping(values)
        except ConfigError:
            return AppConfig(
                dry_run=True,
                jira_url=DEFAULT_JIRA_URL,
                project_allowlist=("DAH",),
                cache_ttl_hours=24,
                demo_mode=True,
                selected_project="DAH",
            )

    def _compose_services(self) -> None:
        # There is deliberately no URL or HTTP dependency in this gateway.
        existing_gateway = getattr(self, "_gateway", None)
        if isinstance(existing_gateway, LocalDemoGateway):
            existing_gateway.set_project_allowlist(self._config.project_allowlist)
            self._gateway = existing_gateway
        else:
            self._gateway = LocalDemoGateway(project_allowlist=self._config.project_allowlist)
            snapshots = tuple(
                snapshot
                for ticket in self._store.list_tickets()
                if isinstance(ticket.get("data"), Mapping)
                for snapshot in (
                    _snapshot_from_data(ticket["data"].get("demo_snapshot")),
                )
                if snapshot is not None
            )
            self._gateway.hydrate_issues(snapshots)
        self._metadata = CachingMetadataProvider(self._gateway, self._store)
        self._repository = SQLiteTicketRepository(self._store)
        self._audit = SQLiteAuditSink(self._store)
        self._preview_service = PreviewService(self._gateway, self._metadata)
        self._sync_service = SyncService(
            self._gateway,
            self._metadata,
            StaticProjectPolicy(self._config.project_allowlist),
            repository=self._repository,
            audit=self._audit,
            preview_service=self._preview_service,
        )
        self._reporting = ReportingService(self._gateway)
        self._comments = CommentService(self._gateway)

    def close(self) -> None:
        self._session_token = None
        self._store.close()

    # ------------------------------------------------------------------ setup
    def startup_snapshot(self) -> ui.StartupSnapshot:
        tickets = self._store.list_tickets()
        conflicts = sum(str(item.get("status", "")).upper() == "CONFLICT" for item in tickets)
        results = self._store.list_sync_results(limit=1)
        cache = self._store.cache_status()
        board = str(self._store.get_setting("selected_board", ""))
        board_label = self._option_label("boards", board) if board else "Nicht ausgewählt"
        last_at = results[0]["created_at"] if results else None
        last_summary = (
            f"{_STATUS_LABELS.get(str(results[0]['outcome']), str(results[0]['outcome']))}: "
            f"{results[0]['message']}"
            if results
            else "Noch keine Verarbeitung"
        )
        cache_until = (
            cache.newest_fetch + timedelta(hours=self._config.cache_ttl_hours)
            if cache.newest_fetch
            else None
        )
        return ui.StartupSnapshot(
            user_display_name="Demo User (offline)",
            user_account="demo.user",
            project_key=self._config.selected_project,
            project_name=(
                "Data & Analytics Hub"
                if self._config.selected_project == "DAH"
                else self._config.selected_project
            ),
            jira_url=self._config.jira_url or DEFAULT_JIRA_URL,
            dry_run=self._config.dry_run,
            online=False,
            cache_updated_at=cache.newest_fetch,
            cache_valid_until=cache_until,
            board_name=board_label,
            pending_drafts=sum(str(item.get("status", "")).upper() == "DRAFT" for item in tickets),
            conflicts=conflicts,
            last_run_at=last_at,
            last_run_summary=last_summary,
        )

    def load_setup(self) -> ui.SetupData:
        credential_storage_available = bool(self._credentials.persistent)
        return ui.SetupData(
            jira_url=self._config.jira_url or DEFAULT_JIRA_URL,
            username=str(self._store.get_setting("username", "")),
            project=self._config.selected_project,
            remember_token=(
                credential_storage_available
                and bool(self._store.get_setting("remember_signin", True))
            ),
            configured=bool(self._store.get_setting("setup_completed", False)),
            credential_storage_available=credential_storage_available,
        )

    def test_connection(self, jira_url: str, token: str) -> ui.ConnectionResult:
        # Validate input shape but never open a socket in the current product step.
        if token:
            register_secret(token)
        try:
            AppConfig(
                dry_run=True,
                jira_url=jira_url or DEFAULT_JIRA_URL,
                project_allowlist=self._config.project_allowlist,
                selected_project=self._config.selected_project,
            )
        except ConfigError as error:
            return ui.ConnectionResult(False, "Konfiguration ungültig", safe_error_message(error))
        try:
            result = self._gateway.test_connection()
        except Exception as error:
            return ui.ConnectionResult(
                False,
                "Verbindungstest fehlgeschlagen",
                safe_error_message(error),
            )
        return ui.ConnectionResult(
            True,
            "Offline-Demomodus bereit",
            "Es wurde keine Netzwerkverbindung zu Jira geöffnet. Die echte Anbindung folgt separat.",
            username=str(result.get("username", "demo.user")),
            server_version=str(result.get("server_version", "offline-synthetic")),
        )

    def save_setup(self, setup: ui.SetupData, token: str) -> ui.ConnectionResult:
        if token:
            # Register at the non-UI boundary as well: callers are not required
            # to use the Qt page and exception messages must still be redacted.
            register_secret(token)
        project = setup.project.strip().upper()
        if project not in self._config.project_allowlist:
            return ui.ConnectionResult(
                False,
                "Projekt nicht freigegeben",
                f"{project or '(leer)'} ist nicht in der lokalen Allowlist.",
            )
        try:
            candidate = AppConfig(
                dry_run=self._config.dry_run,
                jira_url=setup.jira_url or DEFAULT_JIRA_URL,
                project_allowlist=self._config.project_allowlist,
                cache_ttl_hours=self._config.cache_ttl_hours,
                demo_mode=True,
                selected_project=project,
            )
        except ConfigError as error:
            return ui.ConnectionResult(False, "Konfiguration ungültig", safe_error_message(error))

        connection = self.test_connection(candidate.jira_url or DEFAULT_JIRA_URL, token)
        if not connection.ok:
            return connection

        username = setup.username.strip() or connection.username.strip() or "demo.user"
        persistent_credentials = bool(self._credentials.persistent)
        effective_remember = bool(setup.remember_token and persistent_credentials)
        credential_changed = False
        previous_secret: str | None = None
        try:
            if persistent_credentials and (token or not effective_remember):
                previous_secret = self._credentials.load("jira", username)
                if effective_remember:
                    self._credentials.save("jira", username, token)
                else:
                    self._credentials.delete("jira", username)
                credential_changed = True
        except Exception as error:
            return ui.ConnectionResult(
                False,
                "Token konnte nicht sicher gespeichert werden",
                safe_error_message(error),
            )

        # Only commit local configuration after the read-only connection test
        # and the requested credential operation have both succeeded.
        try:
            self._store.set_settings(
                {
                    "jira_url": candidate.jira_url,
                    "selected_project": project,
                    "username": username,
                    # This preference is not a secret.  Memory-only fallback
                    # can never truthfully claim a durable sign-in.
                    "remember_signin": effective_remember,
                    "setup_completed": True,
                }
            )
        except Exception as error:
            if persistent_credentials and credential_changed:
                try:
                    if previous_secret is None:
                        self._credentials.delete("jira", username)
                    else:
                        self._credentials.save("jira", username, previous_secret)
                except Exception:
                    # The primary failure remains the local atomic commit.  A
                    # later setup attempt can repair the external secret store.
                    pass
            return ui.ConnectionResult(
                False,
                "Einrichtung konnte nicht gespeichert werden",
                safe_error_message(error),
            )

        safety_changed = self._safety_context(candidate) != self._safety_context(self._config)
        self._config = candidate
        if effective_remember:
            self._session_token = None
        elif token:
            self._session_token = token
        if safety_changed:
            self._invalidate_previews()
        self._compose_services()
        return connection

    # --------------------------------------------------------------- metadata
    def refresh_metadata(self, progress: ui.ProgressCallback | None = None) -> ui.StartupSnapshot:
        kinds = tuple(DEFAULT_SMALL_METADATA_KINDS)
        for index, kind in enumerate(kinds, start=1):
            if progress:
                progress(int((index - 1) / len(kinds) * 90), f"{kind} wird aktualisiert …")
            self._metadata.get_metadata(
                kind,
                project=self._config.selected_project,
                force_refresh=True,
            )
        for issue_type in IssueType:
            self._metadata.get_context(
                self._config.selected_project,
                issue_type,
                force_refresh=True,
            )
        self._store.set_setting("metadata_refreshed_at", datetime.now(UTC).isoformat())
        if progress:
            progress(100, "Metadaten wurden lokal aktualisiert.")
        return self.startup_snapshot()

    def ticket_fields(self, issue_type: str, action: str) -> Sequence[ui.FieldSpec]:
        parsed_type = IssueType(issue_type)
        parsed_action = Action(action)
        fallback = list(ui.default_editor_fields(issue_type, action))
        if not any(item.key == "components" for item in fallback):
            fallback.insert(
                4,
                ui.FieldSpec(
                    "components",
                    "Komponenten",
                    editor="multi-combo",
                    option_source="components",
                ),
            )
        required = self._schema.required_fields(parsed_type)
        adjusted: list[ui.FieldSpec] = []
        for spec in fallback:
            rule = self._schema.rule("status" if spec.key == "jira_status" else spec.key)
            adjusted.append(
                replace(
                    spec,
                    required=spec.key in required or spec.required,
                    clearable_on_update=(
                        parsed_action is Action.UPDATE
                        and rule is not None
                        and rule.clearable
                        and not spec.read_only
                    ),
                )
            )
        return tuple(adjusted)

    def editor_options(
        self,
        issue_type: str,
        project: str,
    ) -> Mapping[str, Sequence[ui.SelectOption]]:
        IssueType(issue_type)
        project_key = project.strip().upper()
        result: dict[str, tuple[ui.SelectOption, ...]] = {}
        for kind in ("priorities", "components", "teams", "link_types", "sprints", "fix_versions"):
            result[kind] = self._metadata_options(kind, project_key)
        result["assignee"] = (
            ui.SelectOption("", "Unassigned"),
            ui.SelectOption("demo.user", "Assign to me"),
        )
        return result

    def search(
        self,
        source: str,
        query: str,
        context: Mapping[str, Any],
    ) -> Sequence[ui.SearchItem]:
        normalized = source.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "users": "people",
            "user": "people",
            "assignee": "people",
            "participants": "people",
            "epic_link": "epics",
            "parent_link": "issues",
            "tickets": "issues",
        }
        resource = aliases.get(normalized, normalized)
        project = str(context.get("project") or self._config.selected_project).upper()
        if resource in {"teams", "components", "priorities", "fix_versions", "sprints"}:
            options = self._metadata_options(resource, project)
            needle = query.casefold().strip()
            return tuple(
                ui.SearchItem(item.value, item.label, item.description)
                for item in options
                if not needle or needle in f"{item.label} {item.value}".casefold()
            )
        rows = self._gateway.search(resource, query, project=project, limit=50)
        items = tuple(
            ui.SearchItem(
                value=_metadata_value(row),
                label=str(row.get("label") or row.get("value") or row.get("id") or ""),
                subtitle=str(row.get("description") or row.get("id") or ""),
                metadata={str(key): value for key, value in row.items()},
            )
            for row in rows
        )
        if resource == "people" and str(context.get("field", "")) == "assignee":
            return (
                ui.SearchItem("", "Unassigned", "Keine Person zuweisen"),
                ui.SearchItem("@me", "Assign to me", "Authentifizierten Nutzer zuweisen"),
            ) + items
        return items

    # --------------------------------------------------------------- tickets
    def list_tickets(self, query: ui.TicketQuery) -> Sequence[ui.TicketListItem]:
        items = tuple(self._ticket_list_item(row) for row in self._store.list_tickets())
        text = query.text.strip().casefold()
        return tuple(
            item
            for item in items
            if (not text or text in f"{item.summary} {item.jira_key} {item.local_id}".casefold())
            and (query.action in {"", "Alle"} or item.action == query.action)
            and (query.issue_type in {"", "Alle"} or item.issue_type == query.issue_type)
            and (query.result in {"", "Alle"} or item.result == query.result)
        )

    def load_ticket(self, local_id: str) -> ui.TicketDraft:
        row = self._store.get_ticket(local_id)
        if row is not None:
            return _draft_from_data(row["data"], local_id=row["local_id"], jira_key=row["jira_key"])
        if _looks_like_jira_key(local_id):
            snapshot = self._gateway.fetch_issue(local_id)
            return self._draft_from_snapshot(snapshot)
        raise KeyError(f"Lokaler Entwurf {local_id!r} wurde nicht gefunden.")

    def save_draft(self, draft: ui.TicketDraft) -> ui.TicketListItem:
        with self._lock:
            local_id = draft.local_id.strip() or str(uuid.uuid4())
            draft.local_id = local_id
            existing = self._store.get_ticket(local_id)
            self._validate_durable_identity(draft, existing)
            data = _draft_to_data(draft)
            if existing and isinstance(existing.get("data"), Mapping):
                snapshot = existing["data"].get("demo_snapshot")
                if snapshot is not None:
                    data["demo_snapshot"] = snapshot
            self._store.save_draft(data, draft_id=local_id)
            self._store.upsert_ticket(
                local_id,
                data,
                jira_key=draft.jira_key or None,
                action=draft.action,
                status="DRAFT",
            )
            self._append_audit(
                "DRAFT_SAVED",
                entity_type="ticket-row",
                entity_id=local_id,
                details={"action": draft.action, "project": draft.project},
            )
            row = self._store.get_ticket(local_id)
            if row is None:  # pragma: no cover - SQLite invariant
                raise RuntimeError("Der Entwurf konnte nicht gespeichert werden.")
            return self._ticket_list_item(row)

    def preview(self, drafts: Sequence[ui.TicketDraft]) -> Sequence[ui.PreviewData]:
        results: list[ui.PreviewData] = []
        for draft in drafts:
            try:
                self.save_draft(draft)
                command = self._draft_to_command(draft)
                preview = self._preview_service.preview(command, dry_run=self._config.dry_run)
                missing = tuple(
                    item
                    for item in command.attachments
                    if not Path(item.reference).expanduser().is_file()
                )
                if missing:
                    messages = preview.messages + tuple(
                        DomainValidationMessage(
                            code="ATTACHMENT_NOT_FOUND",
                            message=f"Anhang wurde lokal nicht gefunden: {Path(item.reference).name}",
                            field="attachments",
                            severity=Severity.ERROR,
                        )
                        for item in missing
                    )
                    preview = replace(preview, messages=messages)
            except Exception as error:
                command = TicketCommand(
                    row_id=draft.local_id or str(uuid.uuid4()),
                    action=Action.IGNORE,
                    project=draft.project or self._config.selected_project,
                    issue_type=IssueType.STORY,
                )
                preview = Preview(
                    preview_id=str(uuid.uuid4()),
                    command=command,
                    dry_run=self._config.dry_run,
                    payload={},
                    messages=(
                        DomainValidationMessage(
                            code="DRAFT_TRANSLATION_FAILED",
                            message=safe_error_message(error),
                            severity=Severity.ERROR,
                        ),
                    ),
                )
            with self._lock:
                self._previews[preview.preview_id] = preview
                self._preview_drafts[preview.preview_id] = draft
            results.append(self._preview_data(preview, draft))
        return tuple(results)

    def execute(
        self,
        request: ui.ExecutionRequest,
        progress: ui.ProgressCallback | None = None,
    ) -> Sequence[ui.RowResult]:
        results: list[ui.RowResult] = []
        total = max(1, len(request.preview_ids))
        for index, preview_id in enumerate(request.preview_ids, start=1):
            if progress:
                progress(int((index - 1) / total * 100), "Ticket wird sicher verarbeitet …")
            with self._lock:
                if preview_id in self._executed_results:
                    results.append(self._executed_results[preview_id])
                    continue
                preview = self._previews.get(preview_id)
                draft = self._preview_drafts.get(preview_id)
            if preview is None or draft is None:
                results.append(
                    self._execution_error(
                        preview_id,
                        None,
                        "Die Vorschau ist abgelaufen. Bitte neu laden und bestätigen.",
                    )
                )
                continue
            if (
                request.dry_run != self._config.dry_run
                or preview.dry_run != self._config.dry_run
                or request.dry_run != preview.dry_run
            ):
                results.append(
                    self._execution_error(
                        preview_id,
                        draft,
                        "Der Dry-Run- oder Sicherheitskontext hat sich geändert. "
                        "Bitte eine neue Vorschau öffnen.",
                    )
                )
                continue
            try:
                domain_result = self._sync_service.execute(
                    preview,
                    confirmation_token=(
                        preview.confirmation_token if request.confirmed else None
                    ),
                )
                row = self._row_result(domain_result, draft)
            except Exception as error:
                row = self._execution_error(
                    preview_id,
                    draft,
                    safe_error_message(error),
                )
                with self._lock:
                    self._executed_results[preview_id] = row
                results.append(row)
                continue
            try:
                stored = self._store.get_ticket(domain_result.row_id)
                persisted_draft = _draft_after_result(draft, domain_result)
                persisted_data = _draft_to_data(persisted_draft)
                prior_data = stored.get("data") if stored else None
                if isinstance(prior_data, Mapping) and "demo_snapshot" in prior_data:
                    persisted_data["demo_snapshot"] = prior_data["demo_snapshot"]
                if domain_result.status in {SyncStatus.CREATED, SyncStatus.UPDATED} and (
                    domain_result.issue_key
                ):
                    snapshot = self._gateway.fetch_issue(domain_result.issue_key)
                    persisted_data["demo_snapshot"] = _snapshot_to_data(snapshot)
                self._store.save_draft(persisted_data, draft_id=domain_result.row_id)
                self._store.upsert_ticket(
                    domain_result.row_id,
                    persisted_data,
                    jira_key=domain_result.issue_key
                    or (stored["jira_key"] if stored else draft.jira_key),
                    action=persisted_draft.action,
                    status=domain_result.status.value,
                )
            except Exception as error:
                live_boundary_crossed = (
                    not preview.dry_run
                    and preview.command.action in {Action.CREATE, Action.UPDATE}
                    and domain_result.status
                    in {SyncStatus.CREATED, SyncStatus.UPDATED, SyncStatus.UNCERTAIN}
                )
                row = self._execution_error(
                    preview_id,
                    draft,
                    (
                        "Jira kann bereits geändert worden sein, aber der lokale Zustand "
                        "konnte nicht sicher gespeichert werden: "
                        if live_boundary_crossed
                        else "Der lokale Zustand konnte nicht gespeichert werden: "
                    )
                    + safe_error_message(error),
                    uncertain=live_boundary_crossed,
                    jira_key=domain_result.issue_key or draft.jira_key,
                )
            with self._lock:
                self._executed_results[preview_id] = row
            results.append(row)
        if progress:
            progress(100, "Verarbeitung abgeschlossen.")
        return tuple(results)

    def list_results(self) -> Sequence[ui.RowResult]:
        results: list[ui.RowResult] = []
        for raw in self._store.list_sync_results(limit=500):
            ticket = self._store.get_ticket(raw["local_id"]) if raw["local_id"] else None
            data = ticket["data"] if ticket else {}
            action = str(raw["action"])
            status = str(raw["outcome"])
            details = raw["details"] if isinstance(raw.get("details"), Mapping) else {}
            related = _related_ui_results(details.get("related", ()))
            results.append(
                ui.RowResult(
                    row_id=str(raw["local_id"] or ""),
                    action=action,
                    summary=str(data.get("summary", "")),
                    status=_STATUS_LABELS.get(status, status),
                    jira_key=str(raw["jira_key"] or ""),
                    message=str(raw["message"]),
                    timestamp=raw["created_at"],
                    conflict=status == SyncStatus.CONFLICT.value,
                    retry_allowed=status == SyncStatus.FAILED.value,
                    related=related,
                    partial_failure=bool(related)
                    and any(item.outcome != "SUCCESS" for item in related),
                    uncertain=status == SyncStatus.UNCERTAIN.value
                    or any(item.outcome == "UNCERTAIN" for item in related),
                )
            )
        return tuple(results)

    def list_conflicts(self) -> Sequence[ui.ConflictItem]:
        conflicts: list[ui.ConflictItem] = []
        for raw in self._store.list_sync_results(limit=500):
            if raw["outcome"] != SyncStatus.CONFLICT.value:
                continue
            ticket = self._store.get_ticket(raw["local_id"]) if raw["local_id"] else None
            data = ticket["data"] if ticket else {}
            conflicts.append(
                ui.ConflictItem(
                    row_id=str(raw["local_id"] or ""),
                    jira_key=str(raw["jira_key"] or data.get("jira_key", "")),
                    summary=str(data.get("summary", "")),
                    field="updated",
                    local_value=str(data.get("jira_updated", "Vorschauzeitpunkt")),
                    remote_value="Jira wurde nach der Vorschau geändert",
                    detected_at=raw["created_at"],
                )
            )
        return tuple(conflicts)

    def reload_conflict(self, row_id: str) -> ui.TicketDraft:
        draft = self.load_ticket(row_id)
        if not draft.jira_key:
            raise ValueError("Der Konflikt besitzt keinen Jira Key.")
        snapshot = self._gateway.fetch_issue(draft.jira_key)
        draft.project = snapshot.project
        draft.issue_type = snapshot.issue_type.value
        draft.jira_updated = snapshot.updated.isoformat()
        draft.values["jira_status"] = snapshot.status
        draft.values["reporter"] = snapshot.reporter
        if not draft.summary:
            draft.summary = str(snapshot.fields.get("summary", ""))
        return draft

    # --------------------------------------------------------------- dashboard
    def dashboard_options(self) -> Mapping[str, Sequence[ui.SelectOption]]:
        tickets = tuple(self._gateway.search_report_issues(ReportFilter()))
        return {
            "project": tuple(
                ui.SelectOption(item, item) for item in self._config.project_allowlist
            ),
            "sprint": self._metadata_options("sprints", self._config.selected_project),
            "status": _options(sorted({item.status for item in tickets})),
            "status_category": _options(sorted({item.status_category for item in tickets})),
            "issue_type": _options([item.value for item in IssueType]),
            "assignee": _options(sorted({item.assignee for item in tickets if item.assignee})),
            "reporter": _options(sorted({item.reporter for item in tickets if item.reporter})),
            "priority": self._metadata_options("priorities", self._config.selected_project),
            "component": self._metadata_options("components", self._config.selected_project),
            "team": self._metadata_options("teams", self._config.selected_project),
            "epic": _options(sorted({item.epic for item in tickets if item.epic})),
            "label": _options(sorted({label for item in tickets for label in item.labels})),
            "impediment": (ui.SelectOption("true", "Ja"), ui.SelectOption("false", "Nein")),
        }

    def load_dashboard(self, filters: ui.DashboardFilter) -> ui.DashboardData:
        report = self._reporting.run(_report_filter(filters))
        sprint_rows = self._metadata.get_metadata(
            "sprints",
            project=self._config.selected_project,
            force_refresh=False,
        )
        sprint_labels = {
            _metadata_value(item): str(
                item.get("label") or item.get("value") or item.get("id") or ""
            )
            for item in sprint_rows
            if isinstance(item, Mapping)
        }
        metrics = (
            ui.MetricValue("all", "Alle Tickets", str(report.total)),
            ui.MetricValue("open", "Offen", str(report.open)),
            ui.MetricValue("in_progress", "In Bearbeitung", str(report.in_progress)),
            ui.MetricValue("done", "Erledigt", str(report.done)),
            ui.MetricValue(
                "completion_rate", "Erledigungsquote", f"{report.completion_rate:.1f} %"
            ),
            ui.MetricValue("overdue", "Überfällig", str(report.overdue)),
            ui.MetricValue("without_assignee", "Ohne Assignee", str(report.without_assignee)),
            ui.MetricValue("without_sprint", "Ohne Sprint", str(report.without_sprint)),
            ui.MetricValue("impediments", "Blockiert", str(report.impediments)),
            ui.MetricValue(
                "planned_points", "Geplante Story Points", _number(report.planned_story_points)
            ),
            ui.MetricValue(
                "completed_points",
                "Abgeschlossene Story Points",
                _number(report.completed_story_points),
            ),
            ui.MetricValue("open_points", "Offene Story Points", _number(report.open_story_points)),
        )
        breakdowns = {
            "issue_type": _breakdown(report.by_issue_type),
            "priority": _breakdown(report.by_priority),
            "component": _breakdown(report.by_component),
            "team": _breakdown(report.by_team),
            "sprint": _breakdown(report.by_sprint, labels=sprint_labels),
            "story_points": (
                ui.BreakdownItem(
                    "Geplant", report.planned_story_points, _number(report.planned_story_points)
                ),
                ui.BreakdownItem(
                    "Abgeschlossen",
                    report.completed_story_points,
                    _number(report.completed_story_points),
                ),
                ui.BreakdownItem(
                    "Offen", report.open_story_points, _number(report.open_story_points)
                ),
            ),
        }
        return ui.DashboardData(
            metrics=metrics,
            breakdowns=breakdowns,
            generated_at=datetime.now(UTC),
            result_count=report.total,
            scope_description="Synthetische Offline-Tickets des gewählten Projekts",
        )

    def export_dashboard_csv(self, filters: ui.DashboardFilter, destination: Path) -> Path:
        report = self._reporting.run(_report_filter(filters))
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\ufeff" + self._reporting.export_csv(report), encoding="utf-8")
        self._append_audit(
            "REPORT_EXPORTED",
            entity_type="file",
            entity_id=destination.name,
            details={"ticket_count": report.total},
        )
        return destination

    # --------------------------------------------------------------- comments
    def list_comments(self, filters: ui.CommentFilter) -> Sequence[ui.CommentItem]:
        last_raw = self._store.get_setting("comments_last_checked_at")
        last_checked = datetime.fromisoformat(last_raw) if isinstance(last_raw, str) else None
        report_filter = ReportFilter(
            projects=frozenset({filters.project}) if filters.project else frozenset(),
            jira_keys=frozenset({filters.issue_key.upper()}) if filters.issue_key else frozenset(),
        )
        tickets = tuple(self._gateway.search_report_issues(report_filter))
        summary_by_key = {item.key: item.summary for item in tickets}
        result: list[ui.CommentItem] = []
        needle = filters.text.casefold().strip()
        author = filters.author.casefold().strip()
        for ticket in tickets:
            overview = self._comments.overview(ticket.key, since=last_checked)
            for comment in overview.comments:
                is_new = last_checked is None or comment.created > last_checked
                item = ui.CommentItem(
                    issue_key=comment.issue_key,
                    issue_summary=summary_by_key.get(comment.issue_key, ""),
                    author=comment.author,
                    created_at=comment.created,
                    updated_at=comment.updated,
                    body=comment.body,
                    is_new=is_new,
                )
                if (
                    needle
                    and needle
                    not in f"{item.issue_key} {item.issue_summary} {item.body}".casefold()
                ):
                    continue
                if author and author not in item.author.casefold():
                    continue
                if filters.only_new and not item.is_new:
                    continue
                result.append(item)
        self._store.set_setting("comments_last_checked_at", datetime.now(UTC).isoformat())
        return tuple(
            sorted(
                result,
                key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
        )

    # --------------------------------------------------------------- settings
    def load_settings(self) -> ui.SettingsData:
        cache = self._store.cache_status()
        return ui.SettingsData(
            dry_run=self._config.dry_run,
            allowed_projects=self._config.project_allowlist,
            selected_project=self._config.selected_project,
            selected_board=str(self._store.get_setting("selected_board", "")),
            boards=self._metadata_options("boards", self._config.selected_project),
            cache_ttl_hours=self._config.cache_ttl_hours,
            cache_updated_at=cache.newest_fetch,
            data_directory=str(self._data_directory),
            visible_columns=tuple(
                self._store.get_setting("visible_columns", list(DEFAULT_VISIBLE_COLUMNS))
            ),
        )

    def save_settings(self, settings: ui.SettingsData) -> ui.SettingsData:
        candidate = AppConfig(
            dry_run=settings.dry_run,
            jira_url=self._config.jira_url,
            project_allowlist=settings.allowed_projects,
            cache_ttl_hours=settings.cache_ttl_hours,
            demo_mode=True,
            selected_project=settings.selected_project,
        )
        changed_projects = candidate.project_allowlist != self._config.project_allowlist
        prior_board = str(self._store.get_setting("selected_board", ""))
        safety_changed = (
            self._safety_context(candidate) != self._safety_context(self._config)
            or settings.selected_board != prior_board
        )
        self._config = candidate
        self._store.set_setting("dry_run", candidate.dry_run)
        self._store.set_setting("project_allowlist", list(candidate.project_allowlist))
        self._store.set_setting("selected_project", candidate.selected_project)
        self._store.set_setting("cache_ttl_hours", candidate.cache_ttl_hours)
        self._store.set_cache_ttl(timedelta(hours=candidate.cache_ttl_hours))
        self._store.set_setting("selected_board", settings.selected_board)
        self._store.set_setting("visible_columns", list(settings.visible_columns))
        if safety_changed:
            self._invalidate_previews()
        if changed_projects:
            self._compose_services()
        return self.load_settings()

    def clear_metadata_cache(self) -> None:
        removed = self._store.clear_metadata()
        self._append_audit(
            "METADATA_CACHE_CLEARED",
            entity_type="cache",
            entity_id="metadata",
            details={"removed_entries": removed},
        )

    def list_audit(self, limit: int = 500) -> Sequence[ui.AuditItem]:
        result: list[ui.AuditItem] = []
        for raw in self._store.list_audit(limit=limit):
            details = raw["details"] if isinstance(raw["details"], Mapping) else {}
            target = str(raw.get("entity_id") or raw.get("entity_type") or "lokal")
            outcome = str(details.get("outcome") or "OK")
            detail = str(details.get("detail") or details.get("message") or "")
            result.append(
                ui.AuditItem(
                    timestamp=raw["occurred_at"],
                    operation=str(raw["event_type"]),
                    target=target,
                    outcome=outcome,
                    detail=detail,
                    dry_run=_audit_dry_run(details, outcome),
                )
            )
        return tuple(result)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _safety_context(config: AppConfig) -> tuple[bool, str | None, str, tuple[str, ...]]:
        return (
            config.dry_run,
            config.jira_url,
            config.selected_project,
            config.project_allowlist,
        )

    def _invalidate_previews(self) -> None:
        with self._lock:
            self._previews.clear()
            self._preview_drafts.clear()
            self._executed_results.clear()

    @staticmethod
    def _validate_durable_identity(
        draft: ui.TicketDraft,
        existing: Mapping[str, Any] | None,
    ) -> None:
        if existing is None or not existing.get("jira_key"):
            return
        bound_key = str(existing["jira_key"]).strip().upper()
        requested_key = draft.jira_key.strip().upper()
        if draft.action not in {Action.UPDATE.value, Action.IGNORE.value}:
            raise ValueError(
                f"Der lokale Eintrag ist bereits dauerhaft an {bound_key} gebunden."
            )
        if requested_key != bound_key:
            raise ValueError(
                f"Der lokale Eintrag darf nicht von {bound_key} auf "
                f"{requested_key or '(leer)'} umgebunden werden."
            )

    def _append_audit(
        self,
        event_type: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> int:
        event_details = dict(details or {})
        event_details["dry_run"] = self._config.dry_run
        return self._store.append_audit(
            event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            details=event_details,
        )

    def _metadata_options(self, kind: str, project: str) -> tuple[ui.SelectOption, ...]:
        rows = self._metadata.get_metadata(kind, project=project, force_refresh=False)
        if kind == "sprints":
            selected_board = str(self._store.get_setting("selected_board", "")).strip()
            if selected_board:
                rows = tuple(
                    item
                    for item in rows
                    if isinstance(item, Mapping)
                    and str(item.get("board_id", "")).strip() == selected_board
                )
        return tuple(
            ui.SelectOption(
                value=_metadata_value(item),
                label=str(item.get("label") or item.get("value") or item.get("id") or ""),
                description=str(item.get("state") or item.get("description") or ""),
                enabled=str(item.get("state", "")).upper() != "CLOSED",
            )
            for item in rows
            if isinstance(item, Mapping)
        )

    def _option_label(self, kind: str, value: str) -> str:
        try:
            for option in self._metadata_options(kind, self._config.selected_project):
                if option.value == value:
                    return option.label
        except Exception:
            pass
        return value

    def _draft_to_command(self, draft: ui.TicketDraft) -> TicketCommand:
        action = Action(draft.action)
        project = draft.project.strip().upper()
        issue_type = IssueType(draft.issue_type)
        if action is Action.UPDATE and draft.jira_key:
            snapshot = self._gateway.fetch_issue(draft.jira_key)
            project = snapshot.project
            issue_type = snapshot.issue_type
        values = _normalise_values(draft.values, self._schema)
        if "assignee" in values:
            values["assignee"] = self._resolved_assignee(values["assignee"], action, project)
        if "participants" in values:
            values["participants"] = self._resolved_people(values["participants"], project)
        for field, resource in (("epic_link", "epics"), ("parent_link", "issues")):
            if field in values and values[field] not in (None, "", "<CLEAR>"):
                values[field] = self._resolved_search_id(resource, values[field], project)
        sprint_value = values.get("sprint")
        if sprint_value == "<CLEAR>":
            # Keep the marker in command.fields so PreviewService emits Jira
            # null.  command.sprint=None alone means "unchanged".
            sprint = None
        else:
            values.pop("sprint", None)
            sprint = _sprint_ref(
                sprint_value,
                self._metadata_options("sprints", project),
                board_id=str(self._store.get_setting("selected_board", "")) or None,
            )
        attachments = tuple(
            Attachment(str(item.path), item.display_name or item.path.name)
            for item in draft.attachments
        )
        links = tuple(
            IssueLink(
                link_type_id=item.link_type,
                target_key=item.issue_key.strip().upper(),
                direction=(
                    LinkDirection.INWARD
                    if item.direction.casefold() == "inward"
                    else LinkDirection.OUTWARD
                ),
                display_name=item.issue_label or None,
            )
            for item in draft.links
        )
        return TicketCommand(
            row_id=draft.local_id,
            action=action,
            project=project,
            issue_type=issue_type,
            summary=draft.summary,
            jira_key=draft.jira_key.strip().upper() or None,
            fields=values,
            attachments=attachments,
            links=links,
            sprint=sprint,
        )

    def _resolved_assignee(self, value: Any, action: Action, project: str) -> Any:
        selection = _assignee_value(value, action)
        if not isinstance(selection, AssigneeSelection):
            return selection
        if selection.mode is not AssigneeMode.USER:
            return selection
        user_id = self._resolved_search_id("people", selection.user_id, project)
        return AssigneeSelection(AssigneeMode.USER, user_id=user_id)

    def _resolved_people(self, value: Any, project: str) -> Any:
        if value in (None, "", "<CLEAR>"):
            return value
        raw_values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
        return [self._resolved_search_id("people", item, project) for item in raw_values]

    def _resolved_search_id(self, resource: str, value: Any, project: str) -> str:
        requested = str(value).strip()
        if not requested:
            raise ValueError(f"Leere Auswahl für {resource} ist nicht auflösbar.")
        rows = self._gateway.search(resource, requested, project=project, limit=100)
        for item in rows:
            identifiers = {
                str(item.get("id", "")).strip(),
                _metadata_value(item).strip(),
            }
            if requested in identifiers:
                resolved = _metadata_value(item).strip() or str(item.get("id", "")).strip()
                if resolved:
                    return resolved
        raise ValueError(
            f"Die Auswahl {requested!r} konnte in Jira-Metadaten ({resource}) "
            "nicht eindeutig aufgelöst werden."
        )

    def _preview_data(self, preview: Preview, draft: ui.TicketDraft) -> ui.PreviewData:
        validation = tuple(
            ui.ValidationMessage(
                severity=message.severity.value,
                message=message.message,
                field=message.field or "",
            )
            for message in preview.messages
        )
        diffs = tuple(
            ui.DiffItem(
                field=item.field,
                label=_FIELD_LABELS.get(item.field, item.field),
                before=_display_value(item.before),
                after=_display_value(item.after),
                change=item.kind.value.casefold(),
            )
            for item in preview.changes
        )
        return ui.PreviewData(
            preview_id=preview.preview_id,
            action=preview.command.action.value,
            project=preview.command.project,
            issue_type=preview.command.issue_type.value,
            summary=str(preview.command.summary or ""),
            jira_key=preview.command.jira_key or "",
            dry_run=preview.dry_run,
            valid=preview.is_valid,
            validation=validation,
            diffs=diffs,
            warnings=tuple(message.message for message in preview.warnings),
            attachment_names=tuple(
                item.display_name or item.path.name for item in draft.attachments
            ),
            link_labels=tuple(item.issue_label or item.issue_key for item in draft.links),
            updated_timestamp=preview.snapshot.updated.isoformat() if preview.snapshot else "",
        )

    def _row_result(self, result: Any, draft: ui.TicketDraft) -> ui.RowResult:
        status = result.status.value
        related = _related_ui_results(result.related)
        return ui.RowResult(
            row_id=result.row_id,
            action=draft.action,
            summary=draft.summary,
            status=_STATUS_LABELS.get(status, status),
            jira_key=result.issue_key or "",
            message=result.message,
            timestamp=result.timestamp,
            conflict=result.status is SyncStatus.CONFLICT,
            retry_allowed=result.status is SyncStatus.FAILED,
            related=related,
            partial_failure=bool(related) and any(item.outcome != "SUCCESS" for item in related),
            uncertain=result.status is SyncStatus.UNCERTAIN
            or any(item.outcome == "UNCERTAIN" for item in related),
        )

    @staticmethod
    def _execution_error(
        preview_id: str,
        draft: ui.TicketDraft | None,
        message: str,
        *,
        uncertain: bool = False,
        jira_key: str = "",
    ) -> ui.RowResult:
        return ui.RowResult(
            row_id=draft.local_id if draft and draft.local_id else preview_id,
            action=draft.action if draft else "",
            summary=draft.summary if draft else "",
            status=(
                _STATUS_LABELS[SyncStatus.UNCERTAIN.value]
                if uncertain
                else _STATUS_LABELS[SyncStatus.FAILED.value]
            ),
            jira_key=jira_key,
            message=safe_error_message(RuntimeError(message)),
            timestamp=datetime.now(UTC),
            retry_allowed=not uncertain,
            uncertain=uncertain,
        )

    def _ticket_list_item(self, row: Mapping[str, Any]) -> ui.TicketListItem:
        data = row["data"] if isinstance(row.get("data"), Mapping) else {}
        status = str(row.get("status", "DRAFT"))
        return ui.TicketListItem(
            local_id=str(row.get("local_id", "")),
            action=str(row.get("action") or data.get("action") or "CREATE"),
            project=str(data.get("project") or self._config.selected_project),
            issue_type=str(data.get("issue_type") or "Story"),
            summary=str(data.get("summary") or ""),
            jira_key=str(row.get("jira_key") or data.get("jira_key") or ""),
            result=_STATUS_LABELS.get(status, status),
            changed_at=row.get("updated_at"),
            has_conflict=status == SyncStatus.CONFLICT.value,
        )

    def _draft_from_snapshot(self, snapshot: TicketSnapshot) -> ui.TicketDraft:
        allowed = self._schema.allowed_fields(snapshot.issue_type)
        values = {key: value for key, value in snapshot.fields.items() if key in allowed}
        summary = str(values.pop("summary", ""))
        values["jira_status"] = snapshot.status
        values["reporter"] = snapshot.reporter
        return ui.TicketDraft(
            action="UPDATE",
            project=snapshot.project,
            issue_type=snapshot.issue_type.value,
            summary=summary,
            values=values,
            jira_key=snapshot.key,
            jira_updated=snapshot.updated.isoformat(),
        )


def _draft_to_data(draft: ui.TicketDraft) -> dict[str, Any]:
    return {
        "local_id": draft.local_id,
        "action": draft.action,
        "project": draft.project,
        "issue_type": draft.issue_type,
        "summary": draft.summary,
        "values": _json_safe(draft.values),
        "attachments": [
            {
                "path": str(item.path),
                "display_name": item.display_name,
                "size_bytes": item.size_bytes,
            }
            for item in draft.attachments
        ],
        "links": [
            {
                "link_type": item.link_type,
                "issue_key": item.issue_key,
                "issue_label": item.issue_label,
                "direction": item.direction,
            }
            for item in draft.links
        ],
        "jira_key": draft.jira_key,
        "jira_updated": draft.jira_updated,
    }


def _draft_after_result(draft: ui.TicketDraft, result: Any) -> ui.TicketDraft:
    """Return durable pending work after a successful core ticket operation."""

    if result.status not in {SyncStatus.CREATED, SyncStatus.UPDATED}:
        return draft
    successful_attachments = {
        item.reference
        for item in result.related
        if item.kind == "attachment" and (item.succeeded or item.uncertain)
    }
    successful_links = {
        item.reference
        for item in result.related
        if item.kind == "link" and (item.succeeded or item.uncertain)
    }
    attachments = [
        item for item in draft.attachments if str(item.path) not in successful_attachments
    ]
    links = [item for item in draft.links if _link_reference(item) not in successful_links]
    return replace(
        draft,
        action="UPDATE" if result.issue_key else draft.action,
        jira_key=result.issue_key or draft.jira_key,
        attachments=attachments,
        links=links,
    )


def _link_reference(link: ui.LinkDraft) -> str:
    direction = (
        LinkDirection.INWARD if link.direction.casefold() == "inward" else LinkDirection.OUTWARD
    )
    return f"{link.link_type}:{link.issue_key.strip().upper()}:{direction.value}"


def _draft_from_data(
    data: Mapping[str, Any],
    *,
    local_id: str | None = None,
    jira_key: str | None = None,
) -> ui.TicketDraft:
    attachments = [
        ui.AttachmentDraft(
            Path(str(item.get("path", ""))),
            str(item.get("display_name", "")),
            int(item["size_bytes"]) if item.get("size_bytes") is not None else None,
        )
        for item in data.get("attachments", [])
        if isinstance(item, Mapping)
    ]
    links = [
        ui.LinkDraft(
            str(item.get("link_type", "")),
            str(item.get("issue_key", "")),
            str(item.get("issue_label", "")),
            str(item.get("direction", "outward")),
        )
        for item in data.get("links", [])
        if isinstance(item, Mapping)
    ]
    values = data.get("values", {})
    return ui.TicketDraft(
        local_id=local_id or str(data.get("local_id", "")),
        action=str(data.get("action", "CREATE")),
        project=str(data.get("project", "DAH")),
        issue_type=str(data.get("issue_type", "Story")),
        summary=str(data.get("summary", "")),
        values=dict(values) if isinstance(values, Mapping) else {},
        attachments=attachments,
        links=links,
        jira_key=jira_key or str(data.get("jira_key", "")),
        jira_updated=str(data.get("jira_updated", "")),
    )


def _snapshot_to_data(snapshot: TicketSnapshot) -> dict[str, Any]:
    return {
        "key": snapshot.key,
        "project": snapshot.project,
        "issue_type": snapshot.issue_type.value,
        "reporter": snapshot.reporter,
        "status": snapshot.status,
        "updated": snapshot.updated.isoformat(),
        "fields": _snapshot_value_to_data(snapshot.fields),
    }


def _snapshot_from_data(value: Any) -> TicketSnapshot | None:
    if not isinstance(value, Mapping):
        return None
    try:
        updated = datetime.fromisoformat(str(value["updated"]))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        fields = _snapshot_value_from_data(value.get("fields", {}))
        if not isinstance(fields, Mapping):
            return None
        return TicketSnapshot(
            key=str(value["key"]),
            project=str(value["project"]),
            issue_type=IssueType(str(value["issue_type"])),
            reporter=str(value["reporter"]),
            status=str(value["status"]),
            updated=updated.astimezone(UTC),
            fields=dict(fields),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _snapshot_value_to_data(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__ticketpilot_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__ticketpilot_type__": "date", "value": value.isoformat()}
    if isinstance(value, Mapping):
        return {str(key): _snapshot_value_to_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_snapshot_value_to_data(item) for item in value]
    return value


def _snapshot_value_from_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        kind = value.get("__ticketpilot_type__")
        raw = value.get("value")
        if kind == "datetime" and isinstance(raw, str):
            parsed = datetime.fromisoformat(raw)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        if kind == "date" and isinstance(raw, str):
            return date.fromisoformat(raw)
        return {str(key): _snapshot_value_from_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_snapshot_value_from_data(item) for item in value]
    return value


def _related_ui_results(values: Any) -> tuple[ui.RelatedItemResult, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    results: list[ui.RelatedItemResult] = []
    for value in values:
        if isinstance(value, Mapping):
            kind = str(value.get("kind", ""))
            reference = str(value.get("reference", ""))
            succeeded = bool(value.get("succeeded", False))
            uncertain = bool(value.get("uncertain", False))
            message = str(value.get("message", ""))
        else:
            kind = str(getattr(value, "kind", ""))
            reference = str(getattr(value, "reference", ""))
            succeeded = bool(getattr(value, "succeeded", False))
            uncertain = bool(getattr(value, "uncertain", False))
            message = str(getattr(value, "message", ""))
        if kind == "attachment":
            reference = reference.replace("\\", "/").rsplit("/", 1)[-1]
        results.append(
            ui.RelatedItemResult(
                kind=kind,
                reference=reference,
                outcome=("SUCCESS" if succeeded else "UNCERTAIN" if uncertain else "FAILED"),
                message=safe_error_message(RuntimeError(message)),
            )
        )
    return tuple(results)


def _normalise_values(values: Mapping[str, Any], schema: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        # Presentation-only and unknown Jira/report fields never cross into a
        # TicketCommand.  Identity values live on the command/snapshot.
        if key in {"jira_status", "status", "reporter", "jira_key", "project", "issue_type"}:
            continue
        rule = schema.rule(key)
        if rule is None:
            continue
        if value == "<CLEAR>" or value in (None, ""):
            result[key] = value
            continue
        if rule and rule.value_kind is ValueKind.DATE and isinstance(value, str):
            try:
                result[key] = date.fromisoformat(value)
            except ValueError:
                result[key] = value
            continue
        result[key] = value
    return result


def _assignee_value(value: Any, action: Action) -> Any:
    if isinstance(value, AssigneeSelection) or value == "<CLEAR>":
        return value
    if value in (None, ""):
        return AssigneeSelection(AssigneeMode.UNASSIGNED) if action is Action.CREATE else value
    identifier = str(value).strip()
    normalized = identifier.casefold()
    if normalized == "@me":
        return AssigneeSelection(AssigneeMode.SELF)
    if normalized in {"unassigned", "__unassigned__"}:
        return AssigneeSelection(AssigneeMode.UNASSIGNED)
    return AssigneeSelection(AssigneeMode.USER, user_id=identifier)


def _metadata_value(item: Mapping[str, Any]) -> str:
    if "value" in item and item["value"] is not None:
        return str(item["value"])
    return str(item.get("id") or "")


def _sprint_ref(
    value: Any,
    options: Sequence[ui.SelectOption],
    *,
    board_id: str | None = None,
) -> SprintRef | None:
    if value in (None, "", "<CLEAR>"):
        return None
    identifier = str(value)
    option = next((item for item in options if item.value == identifier), None)
    label = option.label if option else identifier
    state_text = (option.description if option else "ACTIVE").upper()
    try:
        state = SprintState(state_text)
    except ValueError:
        state = SprintState.ACTIVE
    return SprintRef(identifier, label, state, board_id=board_id)


def _report_filter(filters: ui.DashboardFilter) -> ReportFilter:
    issue_type = None
    if filters.issue_type and filters.issue_type != "Alle":
        try:
            issue_type = IssueType(filters.issue_type)
        except ValueError:
            issue_type = None
    impediment: bool | None = None
    if filters.impediment:
        impediment = filters.impediment.casefold() in {"ja", "yes", "true", "1", "blockiert"}
    return ReportFilter(
        projects=_single(filters.project),
        sprints=_single(filters.sprint),
        date_from=filters.date_from,
        date_to=filters.date_to,
        statuses=_single(filters.status),
        status_categories=_single(filters.status_category),
        issue_types=frozenset({issue_type}) if issue_type else frozenset(),
        assignees=_single(filters.assignee),
        reporters=_single(filters.reporter),
        priorities=_single(filters.priority),
        components=_single(filters.component),
        teams=_single(filters.team),
        epics=_single(filters.epic),
        labels=_single(filters.label),
        impediment=impediment,
        jira_keys=_single(filters.jira_key.upper()),
        free_text=filters.text,
    )


def _single(value: str) -> frozenset[str]:
    cleaned = value.strip()
    return (
        frozenset()
        if not cleaned or cleaned.casefold() in {"alle", "all"}
        else frozenset({cleaned})
    )


def _options(values: Iterable[str]) -> tuple[ui.SelectOption, ...]:
    return tuple(ui.SelectOption(str(value), str(value)) for value in values if value)


def _breakdown(
    values: Mapping[str, int],
    *,
    labels: Mapping[str, str] | None = None,
) -> tuple[ui.BreakdownItem, ...]:
    resolved_labels = labels or {}
    return tuple(
        ui.BreakdownItem(resolved_labels.get(key, key), float(value), str(value))
        for key, value in sorted(values.items(), key=lambda item: (-item[1], item[0]))
    )


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _display_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(_display_value(item) for item in value)
    return str(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value


def _looks_like_jira_key(value: str) -> bool:
    parts = value.strip().upper().rsplit("-", 1)
    return len(parts) == 2 and parts[0].replace("_", "").isalnum() and parts[1].isdigit()


def _audit_dry_run(details: Mapping[str, Any], outcome: str) -> bool:
    stored = details.get("dry_run")
    if isinstance(stored, bool):
        return stored
    normalized = outcome.strip().upper()
    if normalized == SyncStatus.DRY_RUN.value:
        return True
    # Legacy non-write and failed rows cannot be reconstructed exactly. A
    # fail-safe Dry-Run label avoids falsely claiming a productive write.
    return normalized not in {
        SyncStatus.CREATED.value,
        SyncStatus.UPDATED.value,
        SyncStatus.CONFLICT.value,
        SyncStatus.UNCERTAIN.value,
        SyncStatus.DUPLICATE_PREVENTED.value,
    }


__all__ = ["DEFAULT_JIRA_URL", "OfflineTicketPilotFacade"]
