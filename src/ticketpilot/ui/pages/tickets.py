"""Ticket list, editor launching, preview and guarded execution."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QItemSelection, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..components import NoticeBanner, PageHeader
from ..contracts import (
    ACTIONS,
    ISSUE_TYPES,
    ExecutionRequest,
    PreviewData,
    RowResult,
    TicketDraft,
    TicketListItem,
    TicketPilotFacade,
    TicketQuery,
)
from ..dialogs import PreviewDialog
from ..editor import TicketEditorDialog
from ..models import TicketTableModel
from ..session import UiSession
from ..workers import TaskRunner


class TicketsPage(QWidget):
    results_available = Signal(tuple)
    conflict_navigation_requested = Signal()
    _column_keys = ("action", "project", "issue_type", "summary", "jira_key", "result", "changed_at")

    def __init__(
        self,
        facade: TicketPilotFacade,
        tasks: TaskRunner,
        session: UiSession,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._facade = facade
        self._tasks = tasks
        self._session = session
        self._pending_draft: TicketDraft | None = None
        self._preview_dry_run = True
        self._list_key = "tickets:list"
        self._edit_key = "tickets:load-editor"
        self._save_key = "tickets:save-draft"
        self._preview_key = "tickets:preview"
        self._execute_key = "tickets:execute"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(PageHeader("Tickets", "Lokale Entwürfe einzeln validieren, prüfen und sicher verarbeiten."))
        self.notice = NoticeBanner()
        layout.addWidget(self.notice)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Zusammenfassung oder Jira Key filtern")
        self.search.setClearButtonEnabled(True)
        self.action_filter = QComboBox()
        self.action_filter.addItem("Alle Aktionen")
        self.action_filter.addItems(ACTIONS)
        self.type_filter = QComboBox()
        self.type_filter.addItem("Alle Vorgangstypen")
        self.type_filter.addItems(ISSUE_TYPES)
        self.result_filter = QComboBox()
        self.result_filter.addItems(
            (
                "Alle Ergebnisse",
                "Entwurf",
                "Bereit",
                "Dry Run erfolgreich",
                "Erstellt",
                "Aktualisiert",
                "Ignoriert",
                "Fehler",
                "Konflikt",
                "Unklarer Zustand",
                "Doppelte Erstellung verhindert",
            )
        )
        self.refresh_button = QPushButton("&Aktualisieren")
        filter_label = QLabel("&Filter:")
        filter_label.setBuddy(self.search)
        filters.addWidget(filter_label)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.action_filter)
        filters.addWidget(self.type_filter)
        filters.addWidget(self.result_filter)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        self.model = TicketTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda _index: self.edit_selected())
        layout.addWidget(self.table, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.progress_label = QLabel()
        self.progress_label.setProperty("role", "muted")
        self.progress_label.hide()
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress)

        actions = QHBoxLayout()
        self.new_button = QPushButton("&Neues Ticket")
        self.edit_button = QPushButton("&Bearbeiten")
        self.preview_button = QPushButton("&Vorschau")
        self.execute_button = QPushButton("Auswahl &verarbeiten")
        self.execute_button.setProperty("primary", True)
        for button in (self.new_button, self.edit_button, self.preview_button, self.execute_button):
            button.setProperty("writeAction", True)
            actions.addWidget(button)
        actions.addStretch(1)
        self.selection_label = QLabel("Keine Auswahl")
        self.selection_label.setProperty("role", "muted")
        actions.addWidget(self.selection_label)
        layout.addLayout(actions)

        self.refresh_button.clicked.connect(self.refresh)
        self.search.returnPressed.connect(self.refresh)
        self.action_filter.currentIndexChanged.connect(self.refresh)
        self.type_filter.currentIndexChanged.connect(self.refresh)
        self.result_filter.currentIndexChanged.connect(self.refresh)
        self.new_button.clicked.connect(self.new_ticket)
        self.edit_button.clicked.connect(self.edit_selected)
        self.preview_button.clicked.connect(self.preview_selected)
        self.execute_button.clicked.connect(self.preview_selected)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self._tasks.task_succeeded.connect(self._success)
        self._tasks.task_failed.connect(self._failure)
        self._tasks.task_finished.connect(self._finished)
        self._tasks.task_progress.connect(self._progress)
        self._tasks.write_busy_changed.connect(self.set_write_busy)
        self._selection_changed()
        QWidget.setTabOrder(self.search, self.action_filter)
        QWidget.setTabOrder(self.action_filter, self.type_filter)
        QWidget.setTabOrder(self.type_filter, self.result_filter)
        QWidget.setTabOrder(self.result_filter, self.refresh_button)
        QWidget.setTabOrder(self.refresh_button, self.table)
        QWidget.setTabOrder(self.table, self.new_button)

    def refresh(self) -> None:
        query = TicketQuery(
            text=self.search.text().strip(),
            action=self.action_filter.currentText().replace("Alle Aktionen", "Alle"),
            issue_type=self.type_filter.currentText().replace("Alle Vorgangstypen", "Alle"),
            result=self.result_filter.currentText().replace("Alle Ergebnisse", "Alle"),
        )
        self._tasks.submit(
            self._list_key,
            lambda token, _progress: () if token.cancelled else self._facade.list_tickets(query),
            cancellable=True,
        )

    def new_ticket(self) -> None:
        self._open_editor(TicketDraft(project=self._session.project_key))

    def edit_selected(self) -> None:
        item = self._single_selection()
        if item is None:
            self.notice.set_notice("Bitte genau ein Ticket zum Bearbeiten auswählen.", "warning")
            return
        self._tasks.submit(
            self._edit_key,
            lambda token, _progress: None if token.cancelled else self._facade.load_ticket(item.local_id),
            cancellable=True,
        )

    def open_draft(self, draft: TicketDraft) -> None:
        self._open_editor(draft)

    def _open_editor(self, draft: TicketDraft) -> None:
        editor = TicketEditorDialog(self._facade, self._tasks, self._session, draft, self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        updated = editor.draft()
        if editor.completion == "save":
            self._tasks.submit(
                self._save_key,
                lambda _token, _progress: self._facade.save_draft(updated),
                write=True,
                cancellable=False,
            )
        elif editor.completion == "preview":
            self._preview_drafts((updated,))

    def preview_selected(self) -> None:
        selected = self._selected_items()
        if not selected:
            self.notice.set_notice("Bitte mindestens ein Ticket auswählen.", "warning")
            return

        def load_and_preview(token: object, progress: object) -> Sequence[PreviewData]:
            drafts: list[TicketDraft] = []
            total = len(selected)
            for index, item in enumerate(selected, 1):
                if getattr(token, "cancelled", False):
                    return ()
                drafts.append(self._facade.load_ticket(item.local_id))
                if callable(progress):
                    progress(int(index / total * 45), f"Entwurf {index} von {total} geladen")
            return self._facade.preview(drafts)

        self._tasks.submit(self._preview_key, load_and_preview, cancellable=True)

    def _preview_drafts(self, drafts: Sequence[TicketDraft]) -> None:
        self._tasks.submit(
            self._preview_key,
            lambda token, _progress: () if token.cancelled else self._facade.preview(drafts),
            cancellable=True,
        )

    def _show_preview(self, previews: Sequence[PreviewData]) -> None:
        if not previews:
            self.notice.set_notice("Die Vorschau enthält keine verarbeitbaren Tickets.", "warning")
            return
        dialog = PreviewDialog(previews, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        preview_ids = tuple(item.preview_id for item in previews)
        dry_run = all(item.dry_run for item in previews)
        request = ExecutionRequest(preview_ids=preview_ids, confirmed=True, dry_run=dry_run)
        self._tasks.submit(
            self._execute_key,
            lambda _token, progress: self._facade.execute(request, progress),
            write=True,
            cancellable=False,
        )

    def _success(self, key: str, payload: object) -> None:
        if key == self._list_key:
            records = tuple(item for item in payload if isinstance(item, TicketListItem)) if isinstance(payload, Sequence) else ()
            self.model.set_records(records)
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            self.notice.set_notice(f"{len(records)} Ticket(s) angezeigt.", "info" if records else "warning")
        elif key == self._edit_key and isinstance(payload, TicketDraft):
            self._open_editor(payload)
        elif key == self._save_key and isinstance(payload, TicketListItem):
            self.notice.set_notice("Entwurf wurde lokal gespeichert.", "success")
            self.refresh()
        elif key == self._preview_key and isinstance(payload, Sequence):
            previews = tuple(item for item in payload if isinstance(item, PreviewData))
            self._show_preview(previews)
        elif key == self._execute_key and isinstance(payload, Sequence):
            results = tuple(item for item in payload if isinstance(item, RowResult))
            success_count = sum(
                1
                for item in results
                if not item.conflict
                and not any(
                    marker in item.status.casefold()
                    for marker in ("fehler", "unklar", "verhindert")
                )
            )
            conflict_count = sum(1 for item in results if item.conflict)
            self.notice.set_notice(
                f"Verarbeitung beendet: {success_count} abgeschlossen, {conflict_count} Konflikt(e).",
                "warning" if conflict_count else "success",
            )
            self.results_available.emit(results)
            self.refresh()

    def _failure(self, key: str, message: str, _detail: str) -> None:
        if key in {self._list_key, self._edit_key, self._save_key, self._preview_key, self._execute_key}:
            self.notice.set_notice(f"Vorgang fehlgeschlagen: {message}", "danger")

    def _finished(self, key: str) -> None:
        if key in {self._preview_key, self._execute_key}:
            self.progress.hide()
            self.progress_label.hide()

    def _progress(self, key: str, value: int, message: str) -> None:
        if key not in {self._preview_key, self._execute_key}:
            return
        self.progress.show()
        self.progress_label.show()
        self.progress.setValue(value)
        self.progress_label.setText(message or "Verarbeitung läuft…")

    def _selected_items(self) -> tuple[TicketListItem, ...]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return tuple(item for row in rows if (item := self.model.record(row)) is not None)

    def _single_selection(self) -> TicketListItem | None:
        selected = self._selected_items()
        return selected[0] if len(selected) == 1 else None

    def _selection_changed(self, _selected: QItemSelection | None = None, _deselected: QItemSelection | None = None) -> None:
        count = len(self._selected_items())
        self.selection_label.setText("Keine Auswahl" if not count else f"{count} ausgewählt")
        self.edit_button.setEnabled(count == 1 and not self._tasks.write_busy)
        self.preview_button.setEnabled(count > 0 and not self._tasks.write_busy)
        self.execute_button.setEnabled(count > 0 and not self._tasks.write_busy)

    def set_write_busy(self, busy: bool) -> None:
        self.new_button.setEnabled(not busy)
        self.edit_button.setEnabled(not busy and len(self._selected_items()) == 1)
        self.preview_button.setEnabled(not busy and bool(self._selected_items()))
        self.execute_button.setEnabled(not busy and bool(self._selected_items()))

    def apply_visible_columns(self, visible: Sequence[str]) -> None:
        chosen = set(visible)
        for index, key in enumerate(self._column_keys):
            self.table.setColumnHidden(index, bool(chosen) and key not in chosen)
