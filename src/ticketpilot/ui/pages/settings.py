"""Local settings, metadata maintenance and sanitized audit trail."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..components import NoticeBanner, PageHeader, format_datetime
from ..contracts import AuditItem, SettingsData, StartupSnapshot, TicketPilotFacade
from ..models import AuditTableModel
from ..session import UiSession
from ..workers import TaskRunner

_COLUMNS = (
    ("action", "Aktion"),
    ("project", "Projekt"),
    ("issue_type", "Vorgangstyp"),
    ("summary", "Zusammenfassung"),
    ("jira_key", "Jira Key"),
    ("result", "Ergebnis"),
    ("changed_at", "Geändert"),
)


class SettingsPage(QWidget):
    settings_saved = Signal(object)
    settings_loaded = Signal(object)
    metadata_refreshed = Signal(object)

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
        self._loaded: SettingsData | None = None
        self._load_key = "settings:load"
        self._save_key = "settings:save"
        self._cache_clear_key = "settings:cache-clear"
        self._metadata_key = "settings:metadata-refresh"
        self._audit_key = "settings:audit"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.addWidget(PageHeader("Einstellungen & Audit", "Lokale Sicherheits-, Anzeige- und Cache-Einstellungen."))
        self.notice = NoticeBanner()
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._settings_tab(), "Einstellungen")
        self.tabs.addTab(self._audit_tab(), "Audit")
        self.tabs.currentChanged.connect(lambda index: self.load_audit() if index == 1 else None)
        layout.addWidget(self.tabs, 1)
        self._tasks.task_succeeded.connect(self._success)
        self._tasks.task_failed.connect(self._failure)
        self._tasks.task_progress.connect(self._progress)
        self._tasks.task_finished.connect(self._finished)
        self._tasks.write_busy_changed.connect(self.set_write_busy)

    def _settings_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        safety = QGroupBox("Sicherheit und Projekt")
        safety.setMinimumHeight(150)
        form = QFormLayout(safety)
        self.dry_run = QCheckBox("Dry Run standardmäßig aktiv")
        self.project = QComboBox()
        self.allowlist = QListWidget()
        self.allowlist.setMaximumHeight(90)
        self.allowlist.setEnabled(False)
        form.addRow("Ausführungsmodus", self.dry_run)
        form.addRow("Aktives Zielprojekt", self.project)
        form.addRow("Administrative Allowlist", self.allowlist)
        layout.addWidget(safety)

        metadata = QGroupBox("Board und Metadaten")
        metadata.setMinimumHeight(175)
        metadata_form = QFormLayout(metadata)
        self.board = QComboBox()
        self.cache_ttl = QSpinBox()
        self.cache_ttl.setRange(1, 168)
        self.cache_ttl.setSuffix(" Stunden")
        self.cache_status = QLabel("Nicht geladen")
        self.cache_status.setProperty("role", "muted")
        metadata_form.addRow("Board", self.board)
        metadata_form.addRow("Cache-Gültigkeit", self.cache_ttl)
        metadata_form.addRow("Cache-Stand", self.cache_status)
        cache_actions = QHBoxLayout()
        self.refresh_metadata_button = QPushButton("Metadaten &aktualisieren")
        self.clear_cache_button = QPushButton("Cache leeren")
        self.refresh_metadata_button.clicked.connect(self.refresh_metadata)
        self.clear_cache_button.clicked.connect(self.clear_cache)
        cache_actions.addWidget(self.refresh_metadata_button)
        cache_actions.addWidget(self.clear_cache_button)
        cache_actions.addStretch(1)
        metadata_form.addRow("", cache_actions)
        layout.addWidget(metadata)

        display = QGroupBox("Ticketlisten-Spalten")
        display.setMinimumHeight(190)
        display_layout = QVBoxLayout(display)
        self.columns = QListWidget()
        self.columns.setMaximumHeight(150)
        for key, label in _COLUMNS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.columns.addItem(item)
        display_layout.addWidget(self.columns)
        layout.addWidget(display)

        local = QGroupBox("Lokale Daten")
        local.setMinimumHeight(105)
        local_form = QFormLayout(local)
        self.data_directory = QLineEdit()
        self.data_directory.setReadOnly(True)
        local_form.addRow("Datenordner", self.data_directory)
        layout.addWidget(local)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.reload_button = QPushButton("Zurücksetzen")
        self.save_button = QPushButton("&Einstellungen speichern")
        self.save_button.setProperty("primary", True)
        self.save_button.setProperty("writeAction", True)
        self.reload_button.clicked.connect(self.load)
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.reload_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setAccessibleName("Einstellungen")
        return scroll

    def _audit_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        info = NoticeBanner(
            "Das Audit enthält keine Tokens, Authorization-Header oder vollständigen Jira-Antworten.", "info"
        )
        layout.addWidget(info)
        self.audit_model = AuditTableModel()
        self.audit_table = QTableView()
        self.audit_table.setModel(self.audit_model)
        self.audit_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.audit_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.audit_table.setAlternatingRowColors(True)
        self.audit_table.verticalHeader().hide()
        layout.addWidget(self.audit_table, 1)
        actions = QHBoxLayout()
        self.audit_refresh_button = QPushButton("Audit &aktualisieren")
        self.audit_refresh_button.clicked.connect(self.load_audit)
        actions.addWidget(self.audit_refresh_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def load(self) -> None:
        self._tasks.submit(
            self._load_key,
            lambda token, _progress: SettingsData() if token.cancelled else self._facade.load_settings(),
            cancellable=True,
        )

    def load_audit(self) -> None:
        self._tasks.submit(
            self._audit_key,
            lambda token, _progress: () if token.cancelled else self._facade.list_audit(500),
            cancellable=True,
        )

    def save(self) -> None:
        if self._loaded and self._loaded.dry_run and not self.dry_run.isChecked():
            answer = QMessageBox.warning(
                self,
                "Dry Run deaktivieren?",
                "Dadurch können nach einer separaten Vorschau und Bestätigung echte Jira-Änderungen möglich werden. Fortfahren?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.dry_run.setFocus()
                return
        settings = SettingsData(
            dry_run=self.dry_run.isChecked(),
            allowed_projects=tuple(self._project_values()),
            selected_project=str(self.project.currentData() or self.project.currentText()),
            selected_board=str(self.board.currentData() or ""),
            boards=self._loaded.boards if self._loaded else (),
            cache_ttl_hours=self.cache_ttl.value(),
            cache_updated_at=self._loaded.cache_updated_at if self._loaded else None,
            data_directory=self.data_directory.text(),
            visible_columns=tuple(
                str(self.columns.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(self.columns.count())
                if self.columns.item(index).checkState() == Qt.CheckState.Checked
            ),
        )
        self._tasks.submit(
            self._save_key,
            lambda _token, _progress: self._facade.save_settings(settings),
            write=True,
            cancellable=False,
        )

    def clear_cache(self) -> None:
        answer = QMessageBox.question(
            self,
            "Metadaten-Cache leeren?",
            "Der lokale Metadaten-Cache wird entfernt. Er kann anschließend read-only neu geladen werden.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._tasks.submit(
            self._cache_clear_key,
            lambda _token, _progress: self._facade.clear_metadata_cache(),
            write=True,
            cancellable=False,
        )

    def refresh_metadata(self) -> None:
        self._tasks.submit(
            self._metadata_key,
            lambda token, progress: None if token.cancelled else self._facade.refresh_metadata(progress),
            cancellable=True,
        )
        self.notice.set_notice("Metadaten werden ausschließlich lesend aktualisiert…", "info")

    def _success(self, key: str, payload: object) -> None:
        if key in {self._load_key, self._save_key} and isinstance(payload, SettingsData):
            self._loaded = payload
            self._render(payload)
            if key == self._save_key:
                self.notice.set_notice("Einstellungen wurden gespeichert.", "success")
                self.settings_saved.emit(payload)
            else:
                self.settings_loaded.emit(payload)
        elif key == self._cache_clear_key:
            self.cache_status.setText("Cache ist leer")
            self.notice.set_notice("Metadaten-Cache wurde geleert.", "success")
        elif key == self._metadata_key and isinstance(payload, StartupSnapshot):
            self.notice.set_notice("Metadaten wurden read-only aktualisiert.", "success")
            self.metadata_refreshed.emit(payload)
            self.load()
        elif key == self._audit_key and isinstance(payload, Sequence):
            records = tuple(item for item in payload if isinstance(item, AuditItem))
            self.audit_model.set_records(records)
            self.audit_table.resizeColumnsToContents()

    def _render(self, settings: SettingsData) -> None:
        self.dry_run.setChecked(settings.dry_run)
        self.project.clear()
        self.allowlist.clear()
        for project in settings.allowed_projects:
            self.project.addItem(project, project)
            self.allowlist.addItem(project)
        index = self.project.findData(settings.selected_project)
        self.project.setCurrentIndex(max(0, index))
        self.board.clear()
        self.board.addItem("Nicht ausgewählt", "")
        for board in settings.boards:
            self.board.addItem(board.label, board.value)
        index = self.board.findData(settings.selected_board)
        self.board.setCurrentIndex(max(0, index))
        self.cache_ttl.setValue(settings.cache_ttl_hours)
        self.cache_status.setText(f"Stand: {format_datetime(settings.cache_updated_at)}")
        self.data_directory.setText(settings.data_directory)
        visible = set(settings.visible_columns)
        for row in range(self.columns.count()):
            item = self.columns.item(row)
            item.setCheckState(
                Qt.CheckState.Checked if not visible or item.data(Qt.ItemDataRole.UserRole) in visible else Qt.CheckState.Unchecked
            )

    def _project_values(self) -> list[str]:
        return [self.allowlist.item(index).text() for index in range(self.allowlist.count())]

    def _failure(self, key: str, message: str, _detail: str) -> None:
        if key in {self._load_key, self._save_key, self._cache_clear_key, self._metadata_key, self._audit_key}:
            self.notice.set_notice(f"Aktion fehlgeschlagen: {message}", "danger")

    def _progress(self, key: str, value: int, message: str) -> None:
        if key == self._metadata_key:
            self.notice.set_notice(f"Metadaten {value}%: {message}", "info")

    def _finished(self, key: str) -> None:
        if key == self._metadata_key:
            self.refresh_metadata_button.setEnabled(True)

    def set_write_busy(self, busy: bool) -> None:
        self.save_button.setEnabled(not busy)
        self.clear_cache_button.setEnabled(not busy)
