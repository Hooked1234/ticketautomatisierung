from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor

from ticketpilot.facade import OfflineTicketPilotFacade
from ticketpilot.infrastructure import MemoryCredentialStore, SQLiteStore
from ticketpilot.ui.components import AssigneeField, SearchField
from ticketpilot.ui.contracts import (
    DiffItem,
    ExecutionRequest,
    PreviewData,
    RelatedItemResult,
    RowResult,
    SetupData,
    TicketDraft,
    TicketListItem,
)
from ticketpilot.ui.dialogs import PreviewDialog
from ticketpilot.ui.editor import TicketEditorDialog
from ticketpilot.ui.models import ResultTableModel, TicketTableModel
from ticketpilot.ui.pages.dashboard import DashboardPage
from ticketpilot.ui.pages.results import ResultsPage
from ticketpilot.ui.pages.setup import SetupPage
from ticketpilot.ui.pages.tickets import TicketsPage
from ticketpilot.ui.session import UiSession
from ticketpilot.ui.theme import COLORS
from ticketpilot.ui.workers import TaskRunner, register_process_secret


def _offline_facade(tmp_path: Path) -> OfflineTicketPilotFacade:
    return OfflineTicketPilotFacade(
        SQLiteStore(tmp_path / "ui-safety.sqlite3"),
        data_directory=tmp_path,
        credentials=MemoryCredentialStore(),
    )


def test_worker_boundary_redacts_registered_secret_without_traceback(qtbot) -> None:
    secret = "worker-pat-7vJ4-example"
    register_process_secret(secret)
    runner = TaskRunner()

    def fail(_token, _progress):
        raise RuntimeError(f"upstream echoed {secret}; Authorization: Bearer {secret}")

    with qtbot.waitSignal(runner.task_failed, timeout=3_000) as signal:
        assert runner.submit("unsafe", fail)

    key, message, detail = signal.args
    assert key == "unsafe"
    assert secret not in message
    assert "REDACTED" in message.upper()
    assert detail == ""
    assert "Traceback" not in message


def test_worker_shutdown_drains_running_operation_before_shared_state_closes() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    runner = TaskRunner()

    def operation(_token, _progress):
        started.set()
        release.wait(1)
        finished.set()

    assert runner.submit("shutdown-read", operation, cancellable=False)
    assert started.wait(1)
    release.set()
    assert runner.shutdown(2_000)
    assert finished.is_set()


def test_setup_registers_connection_token_before_worker_failure(qtbot) -> None:
    secret = "setup-pat-A8d5-example"

    class EchoingFacade:
        def load_setup(self) -> SetupData:
            return SetupData()

        def test_connection(self, _jira_url: str, token: str) -> object:
            raise RuntimeError(f"remote response accidentally echoed {token}")

    runner = TaskRunner()
    page = SetupPage(EchoingFacade(), runner)  # type: ignore[arg-type]
    qtbot.addWidget(page)
    page.url.setText("https://jira.example.invalid")
    page.token.setText(secret)

    with qtbot.waitSignal(runner.task_finished, timeout=3_000):
        page.test_connection()

    rendered = page.notice.label.text()
    assert secret not in rendered
    assert "REDACTED" in rendered.upper()


def test_setup_explains_memory_only_credential_fallback(qtbot) -> None:
    class MemoryOnlyFacade:
        def load_setup(self) -> SetupData:
            return SetupData(credential_storage_available=False)

    page = SetupPage(MemoryOnlyFacade(), TaskRunner())  # type: ignore[arg-type]
    qtbot.addWidget(page)

    assert not page.remember.isEnabled()
    assert not page.remember.isChecked()
    assert "nur für diese Sitzung" in page.storage_note.text()


def test_search_and_assignee_only_return_resolved_canonical_values(qtbot) -> None:
    search = SearchField()
    qtbot.addWidget(search)
    search.set_value("demo.user", "Demo User")
    assert search.value() == "demo.user"
    search.input.selectAll()
    qtbot.keyClicks(search.input, "nicht aufgeloest")
    assert search.input.text() == "nicht aufgeloest"
    assert search.value() == ""

    assignee = AssigneeField()
    qtbot.addWidget(assignee)
    assignee.set_value("@me")
    assert assignee.value() == "@me"
    assignee.set_value("")
    assert assignee.value() == ""
    assignee.set_value("alex.example", "Alex Beispiel")
    assert assignee.value() == "alex.example"
    assignee.person.input.selectAll()
    qtbot.keyClicks(assignee.person.input, "Alex roh")
    assert assignee.value() == ""


def test_mixed_preview_batch_keeps_valid_rows_executable(qtbot) -> None:
    previews = (
        PreviewData(
            preview_id="valid-preview",
            action="CREATE",
            project="DAH",
            issue_type="Story",
            summary="Gültiges Ticket",
            dry_run=True,
            valid=True,
            diffs=(DiffItem("summary", "Zusammenfassung", "", "Gültiges Ticket"),),
        ),
        PreviewData(
            preview_id="invalid-preview",
            action="CREATE",
            project="DAH",
            issue_type="Bug",
            summary="Ungültiges Ticket",
            dry_run=True,
            valid=False,
        ),
    )
    dialog = PreviewDialog(previews)
    qtbot.addWidget(dialog)
    assert dialog.execute_button.isEnabled()
    assert "UNGÜLTIG" in dialog.items.item(1).text()
    assert "zeilenbezogen als Fehler" in dialog.notice.label.text()

    with qtbot.waitSignal(dialog.confirmed, timeout=1_000) as signal:
        qtbot.mouseClick(dialog.execute_button, Qt.MouseButton.LeftButton)
    assert signal.args == [("valid-preview", "invalid-preview")]


def test_mixed_facade_batch_returns_invalid_row_as_failed(tmp_path: Path) -> None:
    facade = _offline_facade(tmp_path)
    previews = facade.preview(
        (
            TicketDraft(
                action="CREATE",
                project="DAH",
                issue_type="Story",
                summary="Gültiger Story-Entwurf",
                values={"components": ["Data Platform"]},
            ),
            TicketDraft(
                action="CREATE",
                project="DAH",
                issue_type="Bug",
                summary="",
                values={},
            ),
        )
    )
    assert [preview.valid for preview in previews] == [True, False]
    results = facade.execute(
        ExecutionRequest(
            preview_ids=tuple(preview.preview_id for preview in previews),
            confirmed=True,
            dry_run=True,
        )
    )
    assert [result.status for result in results] == ["Dry Run erfolgreich", "Fehler"]
    facade.close()


def test_loaded_jira_identity_is_locked_and_hidden_fields_are_pruned(qtbot, tmp_path: Path) -> None:
    facade = _offline_facade(tmp_path)
    tasks = TaskRunner()
    session = UiSession()
    draft = TicketDraft(
        action="CREATE",
        project="DAH",
        issue_type="Story",
        summary="Persistiertes Ticket",
        jira_key="DAH-1001",
        values={
            "components": ["Data Platform"],
            "participants": ["alex.example"],
            "sprint": "demo:sprint:active",
            "story_points": 3,
        },
    )
    editor = TicketEditorDialog(facade, tasks, session, draft)
    qtbot.addWidget(editor)
    assert [editor.action.itemText(index) for index in range(editor.action.count())] == [
        "UPDATE",
        "IGNORE",
    ]
    assert editor.action.currentText() == "UPDATE"
    assert editor.jira_key.isReadOnly()
    assert not editor.issue_type.isEnabled()
    editor.jira_key.setText("DAH-9999")
    assert editor.draft().jira_key == "DAH-1001"

    editor.issue_type.setEnabled(True)  # exercise the rebuild path directly
    editor.issue_type.setCurrentText("Incident")
    incident = editor.draft()
    assert "participants" not in incident.values
    editor.issue_type.setCurrentText("Epic")
    epic = editor.draft()
    assert "participants" not in epic.values
    assert "sprint" not in epic.values
    assert "story_points" not in epic.values
    editor.close()
    qtbot.waitUntil(lambda: not tasks.busy, timeout=3_000)
    facade.close()


def test_dashboard_defaults_to_active_sprint_without_date_limit(qtbot, tmp_path: Path) -> None:
    facade = _offline_facade(tmp_path)
    tasks = TaskRunner()
    page = DashboardPage(facade, tasks)
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: not tasks.busy, timeout=5_000)
    filters = page.filters()
    assert filters.sprint == "demo:sprint:active"
    assert filters.date_from is None
    assert filters.date_to is None
    assert "Demo Sprint aktuell" in page.filter_summary.text()

    page._filters["sprint"].setCurrentIndex(0)
    page.date_from.setDate(QDate(2026, 7, 1))
    page.date_to.setDate(QDate(2026, 7, 31))
    explicit = page.filters()
    assert explicit.sprint == ""
    assert explicit.date_from == date(2026, 7, 1)
    assert explicit.date_to == date(2026, 7, 31)

    page.close()
    facade.close()


def test_ticket_status_filter_and_uncertain_result_are_not_success(qtbot, tmp_path: Path) -> None:
    facade = _offline_facade(tmp_path)
    tasks = TaskRunner()
    page = TicketsPage(facade, tasks, UiSession())
    qtbot.addWidget(page)
    values = [page.result_filter.itemText(index) for index in range(page.result_filter.count())]
    assert "Gültig" not in values
    assert "Verarbeitet" not in values
    assert "Unklarer Zustand" in values

    model = ResultTableModel(
        (
            RowResult(
                row_id="row-1",
                action="CREATE",
                summary="Unklar",
                status="Unklarer Zustand",
                retry_allowed=False,
            ),
        )
    )
    color = model.data(model.index(0, 3), Qt.ItemDataRole.ForegroundRole)
    assert isinstance(color, QColor)
    assert color == QColor(COLORS["danger"])
    assert model.record(0) is not None and not model.record(0).retry_allowed
    ticket_model = TicketTableModel(
        (
            TicketListItem(
                local_id="row-1",
                action="CREATE",
                project="DAH",
                issue_type="Story",
                summary="Unklar",
                result="Unklarer Zustand",
            ),
        )
    )
    ticket_color = ticket_model.data(
        ticket_model.index(0, 5), Qt.ItemDataRole.ForegroundRole
    )
    assert ticket_color == QColor(COLORS["danger"])
    facade.close()


def test_related_result_contract_derives_partial_and_uncertain_states() -> None:
    partial = RowResult(
        row_id="partial-row",
        action="UPDATE",
        summary="Gemischtes Ergebnis",
        status="Aktualisiert",
        related=(
            RelatedItemResult("attachment", "analyse.pdf", "SUCCESS", "Hochgeladen."),
            RelatedItemResult(
                "link",
                "blockiert DAH-42",
                "FAILED",
                "Anfrage abgelehnt; Kennung <REDACTED>.",
            ),
        ),
    )
    unclear = RowResult(
        row_id="unclear-row",
        action="UPDATE",
        summary="Unklarer Link",
        status="Aktualisiert",
        related=(
            RelatedItemResult(
                "link",
                "verknüpft DAH-43",
                "UNCERTAIN",
                "Zeitüberschreitung; Annahme durch Jira unklar.",
            ),
        ),
    )

    assert partial.has_partial_failure
    assert not partial.has_uncertain_state
    assert unclear.has_uncertain_state
    assert not unclear.has_partial_failure
    assert not unclear.retry_allowed


def test_results_page_shows_individual_related_results_and_safety_indicator(
    qtbot, tmp_path: Path
) -> None:
    facade = _offline_facade(tmp_path)
    tasks = TaskRunner()
    page = ResultsPage(facade, tasks)
    qtbot.addWidget(page)
    partial = RowResult(
        row_id="partial-row",
        action="CREATE",
        summary="Ticket mit Teilerfolg",
        status="Erstellt",
        related=(
            RelatedItemResult("attachment", "konzept.pdf", "SUCCESS", "Hochgeladen."),
            RelatedItemResult(
                "link",
                "blockiert DAH-17",
                "FAILED",
                "HTTP-Fehler; Token <REDACTED>.",
            ),
        ),
    )
    unclear = RowResult(
        row_id="unclear-row",
        action="UPDATE",
        summary="Ticket mit unklarem Link",
        status="Aktualisiert",
        related=(
            RelatedItemResult(
                "link",
                "verknüpft DAH-18",
                "UNCERTAIN",
                "Zeitüberschreitung; Jira-Ergebnis unklar.",
            ),
        ),
    )

    page.add_results((partial, unclear))
    assert page.related_model.rowCount() == 2
    assert page.related_badge.text() == "TEILERFOLG"
    assert page.related_badge.property("tone") == "warning"
    assert page.related_model.data(page.related_model.index(0, 0)) == "Anhang"
    assert page.related_model.data(page.related_model.index(0, 2)) == "Erfolgreich"
    assert page.related_model.data(page.related_model.index(1, 0)) == "Issue Link"
    assert page.related_model.data(page.related_model.index(1, 2)) == "Definitiver Fehler"
    assert "<REDACTED>" in page.related_model.data(page.related_model.index(1, 3))
    assert "Teilerfolg" in page.result_model.data(page.result_model.index(0, 5))

    page.result_table.selectRow(1)
    assert page.related_model.rowCount() == 1
    assert page.related_badge.text() == "UNKLAR"
    assert page.related_badge.property("tone") == "danger"
    assert "Nicht automatisch wiederholen" in page.related_guidance.text()
    assert page.related_model.data(page.related_model.index(0, 2)) == "Unklar"
    row_color = page.result_model.data(
        page.result_model.index(1, 3), Qt.ItemDataRole.ForegroundRole
    )
    assert row_color == QColor(COLORS["danger"])

    page.close()
    facade.close()
