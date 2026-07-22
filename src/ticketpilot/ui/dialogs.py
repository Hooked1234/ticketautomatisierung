"""Dialogs for remote search, preview/diff and safe confirmation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .components import NoticeBanner, SafetyStrip
from .contracts import PreviewData, SearchItem
from .theme import COLORS
from .workers import TaskRunner


class SearchDialog(QDialog):
    """Debounced on-demand search; suitable for people, epics and accounts."""

    def __init__(
        self,
        title: str,
        loader: Callable[[str], Sequence[SearchItem]],
        tasks: TaskRunner,
        *,
        initial_query: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(680, 470)
        self._loader = loader
        self._tasks = tasks
        self._key = f"search:{uuid4()}"
        self._selection: SearchItem | None = None

        layout = QVBoxLayout(self)
        prompt = QLabel("Suchbegriff")
        self.query = QLineEdit(initial_query)
        prompt.setBuddy(self.query)
        self.query.setPlaceholderText("Mindestens zwei Zeichen eingeben")
        self.query.setClearButtonEnabled(True)
        self.status = QLabel("Die Suche lädt Ergebnisse erst bei Bedarf.")
        self.status.setProperty("role", "muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.results = QListWidget()
        self.results.setAccessibleName("Suchergebnisse")
        self.results.itemDoubleClicked.connect(lambda _item: self._accept_selected())
        layout.addWidget(prompt)
        layout.addWidget(self.query)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(self.results, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Übernehmen")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.ok_button.setEnabled(False)
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._search)
        self.query.textChanged.connect(self._queue_search)
        self.results.currentItemChanged.connect(lambda current, _old: self.ok_button.setEnabled(current is not None))
        self._tasks.task_succeeded.connect(self._on_success)
        self._tasks.task_failed.connect(self._on_failure)
        self._tasks.task_finished.connect(self._on_finished)
        if initial_query.strip():
            self._queue_search(initial_query)
        QTimer.singleShot(0, self.query.setFocus)

    @property
    def selected(self) -> SearchItem | None:
        return self._selection

    def _queue_search(self, text: str) -> None:
        if len(text.strip()) < 2:
            self._timer.stop()
            self.results.clear()
            self.status.setText("Mindestens zwei Zeichen eingeben.")
            return
        self._timer.start()

    def _search(self) -> None:
        query = self.query.text().strip()
        if len(query) < 2:
            return
        self.results.clear()
        self.status.setText("Suche läuft…")
        self.progress.show()
        self.query.setEnabled(False)
        self._tasks.submit(self._key, lambda token, _progress: self._load(token, query), cancellable=True)

    def _load(self, token: object, query: str) -> Sequence[SearchItem]:
        if getattr(token, "cancelled", False):
            return ()
        result = self._loader(query)
        if getattr(token, "cancelled", False):
            return ()
        return result

    def _on_success(self, key: str, payload: object) -> None:
        if key != self._key:
            return
        items = list(payload) if isinstance(payload, Sequence) else []
        for result in items:
            if not isinstance(result, SearchItem):
                continue
            item = QListWidgetItem(result.label)
            item.setData(Qt.ItemDataRole.UserRole, result)
            item.setToolTip(result.subtitle)
            if result.subtitle:
                item.setText(f"{result.label}\n{result.subtitle}")
            self.results.addItem(item)
        self.status.setText(f"{self.results.count()} Treffer")
        if self.results.count():
            self.results.setCurrentRow(0)

    def _on_failure(self, key: str, message: str, _detail: str) -> None:
        if key == self._key:
            self.status.setText(f"Suche fehlgeschlagen: {message}")

    def _on_finished(self, key: str) -> None:
        if key == self._key:
            self.progress.hide()
            self.query.setEnabled(True)
            self.query.setFocus()

    def _accept_selected(self) -> None:
        item = cast(QListWidgetItem | None, self.results.currentItem())
        if item is None:
            return
        self._selection = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def reject(self) -> None:
        self._tasks.cancel(self._key)
        super().reject()


class PreviewDialog(QDialog):
    """Field-level preview that gates every execution request."""

    confirmed = Signal(tuple)

    def __init__(self, previews: Sequence[PreviewData], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._previews = tuple(previews)
        self.setWindowTitle("Vorschau und Bestätigung")
        self.setModal(True)
        self.resize(1020, 690)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("Änderungen vor der Verarbeitung prüfen")
        title.setProperty("role", "pageTitle")
        layout.addWidget(title)
        dry_run = all(item.dry_run for item in self._previews) if self._previews else True
        project = ", ".join(sorted({item.project for item in self._previews})) or "—"
        self.safety = SafetyStrip()
        self.safety.update_safety(dry_run, project)
        layout.addWidget(self.safety)

        self.notice = NoticeBanner()
        invalid = [item for item in self._previews if not item.valid]
        valid = [item for item in self._previews if item.valid]
        warnings = sum(len(item.warnings) for item in self._previews)
        if invalid and valid:
            self.notice.set_notice(
                f"{len(valid)} gültige Vorgänge können verarbeitet werden; "
                f"{len(invalid)} ungültige Vorgänge werden zeilenbezogen als Fehler protokolliert.",
                "warning",
            )
        elif invalid:
            self.notice.set_notice(
                f"{len(invalid)} Vorgang/Vorgänge sind nicht gültig. Es gibt keine ausführbare Zeile.", "danger"
            )
        elif warnings:
            self.notice.set_notice(f"{warnings} Warnung(en) müssen vor der Bestätigung gelesen werden.", "warning")
        else:
            self.notice.set_notice("Validierung erfolgreich. Bitte Änderungen und Ziel noch einmal prüfen.", "success")
        layout.addWidget(self.notice)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.items = QListWidget()
        self.items.setMinimumWidth(260)
        self.items.setAccessibleName("Vorschauen")
        self.tabs = QTabWidget()
        self.diff_table = QTableWidget(0, 4)
        self.diff_table.setHorizontalHeaderLabels(("Feld", "Bisher", "Danach", "Änderung"))
        self.diff_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.diff_table.setAlternatingRowColors(True)
        self.diff_table.verticalHeader().hide()
        self.diff_table.horizontalHeader().setStretchLastSection(True)
        self.messages = QTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setAccessibleName("Validierung und Warnungen")
        self.relations = QTextEdit()
        self.relations.setReadOnly(True)
        self.relations.setAccessibleName("Anhänge und Verknüpfungen")
        self.tabs.addTab(self.diff_table, "Feldänderungen")
        self.tabs.addTab(self.messages, "Validierung")
        self.tabs.addTab(self.relations, "Anhänge und Links")
        splitter.addWidget(self.items)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        for index, preview in enumerate(self._previews):
            key = preview.jira_key or "Neuer Vorgang"
            state = "UNGÜLTIG · " if not preview.valid else ""
            label = f"{state}{preview.action} · {key}\n{preview.summary}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, index)
            if not preview.valid:
                item.setForeground(QColor(COLORS["danger"]))
            self.items.addItem(item)
        self.items.currentRowChanged.connect(self._show_preview)
        if self.items.count():
            self.items.setCurrentRow(0)

        self.confirm_check = QCheckBox(
            "Ich habe Zielprojekt, Änderungen und Warnungen geprüft und bestätige die Verarbeitung."
        )
        self.confirm_check.setVisible(not dry_run)
        layout.addWidget(self.confirm_check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.execute_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.execute_button.setProperty("primary", True)
        self.execute_button.setText("Dry Run ausführen" if dry_run else "Jetzt ausführen")
        self.execute_button.setEnabled(bool(valid) and dry_run)
        self.confirm_check.toggled.connect(
            lambda checked: self.execute_button.setEnabled(bool(valid) and checked)
        )
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_preview(self, row: int) -> None:
        if not 0 <= row < len(self._previews):
            return
        preview = self._previews[row]
        self.diff_table.setRowCount(len(preview.diffs))
        for index, diff in enumerate(preview.diffs):
            change_label = {
                "changed": "Geändert",
                "added": "Hinzugefügt",
                "cleared": "Entfernt",
                "removed": "Entfernt",
                "unchanged": "Unverändert",
            }.get(diff.change.casefold(), diff.change)
            values = (
                diff.label,
                diff.before or "—",
                diff.after or "—",
                "Gesperrt" if diff.locked else change_label,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if diff.locked:
                    item.setForeground(QColor(COLORS["muted"]))
                self.diff_table.setItem(index, column, item)
        self.diff_table.resizeColumnsToContents()
        lines: list[str] = []
        for validation in preview.validation:
            lines.append(f"[{validation.severity.upper()}] {validation.message}")
        for warning in preview.warnings:
            lines.append(f"[WARNUNG] {warning}")
        self.messages.setPlainText("\n\n".join(lines) or "Keine Validierungsfehler oder Warnungen.")
        relation_lines = ["Anhänge:", *(f"• {name}" for name in preview.attachment_names)]
        relation_lines.extend(["", "Verknüpfungen:", *(f"• {label}" for label in preview.link_labels)])
        self.relations.setPlainText("\n".join(relation_lines))

    def _confirm(self) -> None:
        self.confirmed.emit(tuple(item.preview_id for item in self._previews))
        self.accept()


class ConflictDetailsDialog(QDialog):
    reload_requested = Signal(str)

    def __init__(self, conflict: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row_id = str(getattr(conflict, "row_id", ""))
        self.setWindowTitle("Konflikt prüfen")
        self.resize(650, 420)
        layout = QVBoxLayout(self)
        title = QLabel(f"Konflikt für {getattr(conflict, 'jira_key', 'Ticket')}")
        title.setProperty("role", "pageTitle")
        layout.addWidget(title)
        banner = NoticeBanner(str(getattr(conflict, "guidance", "Jira-Daten neu laden.")), "danger")
        layout.addWidget(banner)
        table = QTableWidget(2, 2)
        table.setHorizontalHeaderLabels(("Quelle", "Wert"))
        table.setVerticalHeaderLabels(("Lokaler Entwurf", "Aktueller Jira-Stand"))
        table.setItem(0, 0, QTableWidgetItem(str(getattr(conflict, "field", "Feld"))))
        table.setItem(0, 1, QTableWidgetItem(str(getattr(conflict, "local_value", ""))))
        table.setItem(1, 0, QTableWidgetItem(str(getattr(conflict, "field", "Feld"))))
        table.setItem(1, 1, QTableWidgetItem(str(getattr(conflict, "remote_value", ""))))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        actions = QHBoxLayout()
        actions.addStretch(1)
        close = QPushButton("Schließen")
        reload_button = QPushButton("Jira-Daten neu laden")
        reload_button.setProperty("primary", True)
        close.clicked.connect(self.reject)
        reload_button.clicked.connect(self._reload)
        actions.addWidget(close)
        actions.addWidget(reload_button)
        layout.addLayout(actions)

    def _reload(self) -> None:
        self.reload_requested.emit(self._row_id)
        self.accept()
