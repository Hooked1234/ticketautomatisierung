"""Row-isolated operation results and optimistic-concurrency conflicts."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..components import NoticeBanner, PageHeader, StatusBadge
from ..contracts import ConflictItem, RowResult, TicketDraft, TicketPilotFacade
from ..dialogs import ConflictDetailsDialog
from ..models import ConflictTableModel, RelatedResultTableModel, ResultTableModel
from ..workers import TaskRunner


class ResultsPage(QWidget):
    draft_reloaded = Signal(object)

    def __init__(self, facade: TicketPilotFacade, tasks: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._facade = facade
        self._tasks = tasks
        self._results_key = "results:load"
        self._conflicts_key = "conflicts:load"
        self._reload_key = "conflicts:reload"
        self._reload_row_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.addWidget(PageHeader("Ergebnisse & Konflikte", "Jede Zeile bleibt isoliert; Fehler stoppen keine weiteren Tickets."))
        self.notice = NoticeBanner()
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        self.result_model = ResultTableModel()
        self.result_table = self._table(self.result_model)
        self.related_model = RelatedResultTableModel()
        self.related_table = self._table(self.related_model)
        self.related_table.setAccessibleName("Einzelergebnisse für Anhänge und Issue Links")
        self.result_table.selectionModel().currentRowChanged.connect(self._show_related)
        self.conflict_model = ConflictTableModel()
        self.conflict_table = self._table(self.conflict_model)
        self.conflict_table.doubleClicked.connect(lambda _index: self.open_conflict())
        result_page = QWidget()
        result_layout = QVBoxLayout(result_page)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_splitter = QSplitter(Qt.Orientation.Vertical)
        result_splitter.addWidget(self.result_table)
        related_page = QWidget()
        related_layout = QVBoxLayout(related_page)
        related_layout.setContentsMargins(0, 8, 0, 0)
        related_header = QHBoxLayout()
        related_heading = QLabel("Einzelergebnisse: Anhänge & Issue Links")
        related_heading.setProperty("role", "sectionTitle")
        self.related_badge = StatusBadge("KEINE TEILOPERATIONEN", "info")
        related_header.addWidget(related_heading)
        related_header.addWidget(self.related_badge)
        related_header.addStretch(1)
        related_layout.addLayout(related_header)
        self.related_guidance = QLabel(
            "Ergebniszeile auswählen, um die einzelnen Anhangs- und Link-Operationen zu prüfen."
        )
        self.related_guidance.setProperty("role", "muted")
        self.related_guidance.setWordWrap(True)
        related_layout.addWidget(self.related_guidance)
        related_layout.addWidget(self.related_table, 1)
        result_splitter.addWidget(related_page)
        result_splitter.setStretchFactor(0, 3)
        result_splitter.setStretchFactor(1, 2)
        result_layout.addWidget(result_splitter, 1)
        self.tabs.addTab(result_page, "Resultate (0)")
        conflict_page = QWidget()
        conflict_layout = QVBoxLayout(conflict_page)
        conflict_layout.setContentsMargins(0, 0, 0, 0)
        conflict_layout.addWidget(self.conflict_table, 1)
        conflict_actions = QHBoxLayout()
        self.inspect_button = QPushButton("Konflikt &prüfen")
        self.reload_button = QPushButton("Jira-Daten neu &laden")
        self.reload_button.setProperty("writeAction", True)
        self.inspect_button.clicked.connect(self.open_conflict)
        self.reload_button.clicked.connect(self.reload_conflict)
        conflict_actions.addWidget(self.inspect_button)
        conflict_actions.addWidget(self.reload_button)
        conflict_actions.addStretch(1)
        conflict_layout.addLayout(conflict_actions)
        self.tabs.addTab(conflict_page, "Konflikte (0)")
        layout.addWidget(self.tabs, 1)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("&Aktualisieren")
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self._tasks.task_succeeded.connect(self._success)
        self._tasks.task_failed.connect(self._failure)
        self._tasks.write_busy_changed.connect(self.set_write_busy)

    @staticmethod
    def _table(model: QAbstractItemModel) -> QTableView:
        table = QTableView()
        table.setModel(model)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        return table

    def refresh(self) -> None:
        self._tasks.submit(
            self._results_key,
            lambda token, _progress: () if token.cancelled else self._facade.list_results(),
            cancellable=True,
        )
        self._tasks.submit(
            self._conflicts_key,
            lambda token, _progress: () if token.cancelled else self._facade.list_conflicts(),
            cancellable=True,
        )

    def add_results(self, results: Sequence[object]) -> None:
        accepted = tuple(item for item in results if isinstance(item, RowResult))
        if accepted:
            self.result_model.set_records((*accepted, *self.result_model.records()))
            self.tabs.setTabText(0, f"Resultate ({self.result_model.rowCount()})")
            self.tabs.setCurrentIndex(0)
            self.result_table.selectRow(0)
            self._show_related(self.result_model.index(0, 0), QModelIndex())

    def _show_related(self, current: QModelIndex, _previous: QModelIndex) -> None:
        result = self.result_model.record(current.row()) if current.isValid() else None
        related = result.related if result is not None else ()
        self.related_model.set_records(related)
        self.related_table.resizeColumnsToContents()
        if result is None or not related:
            self.related_badge.setText("KEINE TEILOPERATIONEN")
            self.related_badge.set_tone("info")
            self.related_guidance.setText(
                "Für diese Ergebniszeile liegen keine einzelnen Anhangs- oder Link-Operationen vor."
            )
            return
        successful = sum(item.outcome == "SUCCESS" for item in related)
        if result.has_uncertain_state:
            self.related_badge.setText("UNKLAR")
            self.related_badge.set_tone("danger")
            self.related_guidance.setText(
                "Mindestens eine Teiloperation hat einen unklaren Ausgang. Nicht automatisch wiederholen; zuerst in Jira prüfen."
            )
        elif result.has_partial_failure:
            self.related_badge.setText("TEILERFOLG")
            self.related_badge.set_tone("warning")
            self.related_guidance.setText(
                f"{successful} von {len(related)} Teiloperationen waren erfolgreich. Fehlgeschlagene Einträge einzeln prüfen."
            )
        elif successful == len(related):
            self.related_badge.setText("ERFOLGREICH")
            self.related_badge.set_tone("success")
            self.related_guidance.setText(
                f"Alle {len(related)} Teiloperationen wurden erfolgreich verarbeitet."
            )
        else:
            self.related_badge.setText("FEHLER")
            self.related_badge.set_tone("danger")
            self.related_guidance.setText("Keine Teiloperation war erfolgreich. Details unten prüfen.")

    def _selected_conflict(self) -> ConflictItem | None:
        rows = self.conflict_table.selectionModel().selectedRows()
        return self.conflict_model.record(rows[0].row()) if rows else None

    def open_conflict(self) -> None:
        conflict = self._selected_conflict()
        if conflict is None:
            self.notice.set_notice("Bitte einen Konflikt auswählen.", "warning")
            return
        dialog = ConflictDetailsDialog(conflict, self)
        dialog.reload_requested.connect(self._request_reload)
        dialog.exec()

    def reload_conflict(self) -> None:
        conflict = self._selected_conflict()
        if conflict is None:
            self.notice.set_notice("Bitte einen Konflikt auswählen.", "warning")
            return
        self._request_reload(conflict.row_id)

    def _request_reload(self, row_id: str) -> None:
        self._reload_row_id = row_id
        self._tasks.submit(
            self._reload_key,
            lambda token, _progress: None if token.cancelled else self._facade.reload_conflict(row_id),
            cancellable=True,
        )

    def _success(self, key: str, payload: object) -> None:
        if key == self._results_key and isinstance(payload, Sequence):
            result_records = tuple(item for item in payload if isinstance(item, RowResult))
            self.result_model.set_records(result_records)
            self.tabs.setTabText(0, f"Resultate ({len(result_records)})")
            self.result_table.resizeColumnsToContents()
            if result_records:
                self.result_table.selectRow(0)
                self._show_related(self.result_model.index(0, 0), QModelIndex())
            else:
                self._show_related(QModelIndex(), QModelIndex())
        elif key == self._conflicts_key and isinstance(payload, Sequence):
            conflict_records = tuple(item for item in payload if isinstance(item, ConflictItem))
            self.conflict_model.set_records(conflict_records)
            self.tabs.setTabText(1, f"Konflikte ({len(conflict_records)})")
            self.conflict_table.resizeColumnsToContents()
        elif key == self._reload_key and isinstance(payload, TicketDraft):
            self.notice.set_notice("Aktueller Jira-Stand wurde geladen. Bitte Änderungen erneut prüfen.", "success")
            self.draft_reloaded.emit(payload)
            self.refresh()

    def _failure(self, key: str, message: str, _detail: str) -> None:
        if key in {self._results_key, self._conflicts_key, self._reload_key}:
            self.notice.set_notice(f"Daten konnten nicht geladen werden: {message}", "danger")

    def set_write_busy(self, busy: bool) -> None:
        self.reload_button.setEnabled(not busy)
