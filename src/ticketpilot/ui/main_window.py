"""TicketPilot desktop shell and navigation."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ticketpilot import __version__

from .components import SafetyStrip, StatusBadge
from .contracts import RowResult, SettingsData, StartupSnapshot, TicketDraft, TicketPilotFacade
from .pages.comments import CommentsPage
from .pages.dashboard import DashboardPage
from .pages.results import ResultsPage
from .pages.settings import SettingsPage
from .pages.setup import SetupPage
from .pages.status import StatusPage
from .pages.tickets import TicketsPage
from .session import UiSession
from .workers import TaskRunner


class MainWindow(QMainWindow):
    """Main UI entry point; ``facade`` is the only application dependency."""

    def __init__(self, facade: TicketPilotFacade, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = TaskRunner(self)
        self.session = UiSession(self)
        self._startup_key = "app:startup"
        self._page_keys: list[str] = []
        self.setWindowTitle("TicketPilot — persönliche Jira-Tickets")
        self.setMinimumSize(1040, 700)
        self.resize(1360, 880)
        self._build_ui()
        self._build_actions()
        self._connect()
        self._restore_geometry()
        self.load_startup()
        self.settings_page.load()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("AppCanvas")
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        nav_panel = QWidget()
        nav_panel.setObjectName("NavigationPanel")
        nav_panel.setFixedWidth(214)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(0, 18, 0, 12)
        brand = QLabel("TicketPilot")
        brand.setStyleSheet("color: white; font-size: 19px; font-weight: 600; padding: 0 16px 12px 16px;")
        subtitle = QLabel("Persönliche Jira-App")
        subtitle.setStyleSheet("color: #C7D5E5; padding: 0 16px 14px 16px;")
        self.navigation = QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setAccessibleName("Hauptnavigation")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_layout.addWidget(brand)
        nav_layout.addWidget(subtitle)
        nav_layout.addWidget(self.navigation, 1)
        self.connection_badge = StatusBadge("Offline", "warning")
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(16, 8, 16, 0)
        badge_row.addWidget(self.connection_badge)
        badge_row.addStretch(1)
        nav_layout.addLayout(badge_row)
        shell.addWidget(nav_panel)

        content = QWidget()
        content.setObjectName("ContentSurface")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.safety = SafetyStrip()
        content_layout.addWidget(self.safety)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        shell.addWidget(content, 1)
        self.setCentralWidget(central)

        self.status_page = StatusPage(self.session)
        self.tickets_page = TicketsPage(self.facade, self.tasks, self.session)
        self.results_page = ResultsPage(self.facade, self.tasks)
        self.dashboard_page = DashboardPage(self.facade, self.tasks)
        self.comments_page = CommentsPage(self.facade, self.tasks)
        self.settings_page = SettingsPage(self.facade, self.tasks, self.session)
        self.setup_page = SetupPage(self.facade, self.tasks)
        for key, label, page in (
            ("status", "Status", self.status_page),
            ("tickets", "Tickets", self.tickets_page),
            ("results", "Ergebnisse & Konflikte", self.results_page),
            ("dashboard", "Dashboard", self.dashboard_page),
            ("comments", "Kommentare", self.comments_page),
            ("settings", "Einstellungen & Audit", self.settings_page),
            ("setup", "Einrichtung", self.setup_page),
        ):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.navigation.addItem(item)
            self.stack.addWidget(page)
            self._page_keys.append(key)
        self.navigation.setCurrentRow(0)

        status = QStatusBar()
        self.status_message = QLabel("Bereit")
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 100)
        self.task_progress.setFixedWidth(180)
        self.task_progress.hide()
        status.addWidget(self.status_message, 1)
        status.addPermanentWidget(self.task_progress)
        self.setStatusBar(status)

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&Datei")
        close_action = QAction("&Beenden", self)
        close_action.setShortcut(QKeySequence.StandardKey.Quit)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        action_menu = self.menuBar().addMenu("&Aktionen")
        self.new_action = QAction("&Neues Ticket", self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_action.triggered.connect(self._new_ticket)
        refresh_action = QAction("Ansicht &aktualisieren", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self.refresh_current_page)
        metadata_action = QAction("&Metadaten aktualisieren", self)
        metadata_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        metadata_action.triggered.connect(self.settings_page.refresh_metadata)
        action_menu.addActions((self.new_action, refresh_action, metadata_action))

        view_menu = self.menuBar().addMenu("&Ansicht")
        for index, label in enumerate(
            ("Status", "Tickets", "Ergebnisse", "Dashboard", "Kommentare", "Einstellungen", "Einrichtung"), 1
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(f"Ctrl+{index}"))
            action.triggered.connect(lambda _checked=False, row=index - 1: self.navigation.setCurrentRow(row))
            view_menu.addAction(action)

        help_menu = self.menuBar().addMenu("&Hilfe")
        about_action = QAction("&Über TicketPilot", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _connect(self) -> None:
        self.navigation.currentRowChanged.connect(self._page_changed)
        self.session.safety_changed.connect(self.safety.update_safety)
        self.session.connection_changed.connect(self._connection_changed)
        self.status_page.new_ticket_requested.connect(self._new_ticket)
        self.status_page.metadata_refresh_requested.connect(self.settings_page.refresh_metadata)
        self.status_page.setup_requested.connect(lambda: self.navigate("setup"))
        self.setup_page.setup_saved.connect(self.load_startup)
        self.tickets_page.results_available.connect(self._show_results)
        self.results_page.draft_reloaded.connect(self._open_reloaded_draft)
        self.settings_page.settings_saved.connect(self._settings_saved)
        self.settings_page.settings_loaded.connect(self._settings_loaded)
        self.settings_page.metadata_refreshed.connect(self._snapshot_received)
        self.tasks.task_succeeded.connect(self._task_success)
        self.tasks.task_failed.connect(self._task_failure)
        self.tasks.task_started.connect(self._task_started)
        self.tasks.task_finished.connect(self._task_finished)
        self.tasks.task_progress.connect(self._task_progress)
        self.tasks.duplicate_rejected.connect(
            lambda _key: self.statusBar().showMessage("Dieser Vorgang läuft bereits; ein zweiter Start wurde verhindert.", 4500)
        )
        self.tasks.write_busy_changed.connect(self._write_interlock)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "Über TicketPilot",
            (
                f"TicketPilot {__version__}\n\n"
                "Persönliche Windows-App zur sicheren Vorbereitung von Jira-Tickets.\n"
                "Dieser Build verwendet ausschließlich den lokalen Offline-Demoadapter."
            ),
        )

    def navigate(self, key: str) -> None:
        try:
            self.navigation.setCurrentRow(self._page_keys.index(key))
        except ValueError:
            return

    def _page_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        key = self._page_keys[index] if 0 <= index < len(self._page_keys) else ""
        if key == "tickets":
            self.tickets_page.refresh()
        elif key == "results":
            self.results_page.refresh()
        elif key == "settings":
            self.settings_page.load()
            if self.settings_page.tabs.currentIndex() == 1:
                self.settings_page.load_audit()

    def refresh_current_page(self) -> None:
        key = self._page_keys[self.navigation.currentRow()]
        page = self.stack.currentWidget()
        if key == "status":
            self.load_startup()
        elif hasattr(page, "refresh"):
            page.refresh()
        elif hasattr(page, "load"):
            page.load()

    def load_startup(self) -> None:
        self.tasks.submit(
            self._startup_key,
            lambda token, _progress: StartupSnapshot() if token.cancelled else self.facade.startup_snapshot(),
            cancellable=True,
        )

    def _task_success(self, key: str, payload: object) -> None:
        if key == self._startup_key and isinstance(payload, StartupSnapshot):
            self._snapshot_received(payload)

    def _snapshot_received(self, snapshot: StartupSnapshot) -> None:
        self.session.update_from(snapshot)
        self.safety.update_safety(snapshot.dry_run, snapshot.project_key)
        if not snapshot.online and not snapshot.user_account:
            self.statusBar().showMessage("Jira ist nicht verbunden. Einrichtung prüfen.", 5000)

    def _task_failure(self, key: str, message: str, _detail: str) -> None:
        if key == self._startup_key:
            self.statusBar().showMessage(f"Startstatus konnte nicht geladen werden: {message}", 7000)

    def _task_started(self, _key: str, write: bool) -> None:
        self.status_message.setText("Sichere Verarbeitung läuft…" if write else "Daten werden geladen…")
        self.task_progress.setRange(0, 0)
        self.task_progress.show()

    def _task_finished(self, _key: str) -> None:
        if not self.tasks.busy:
            self.status_message.setText("Bereit")
            self.task_progress.hide()

    def _task_progress(self, _key: str, value: int, message: str) -> None:
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(value)
        self.status_message.setText(message or "Vorgang läuft…")

    def _connection_changed(self, online: bool) -> None:
        self.connection_badge.setText("Verbunden" if online else "Offline")
        self.connection_badge.set_tone("success" if online else "warning")

    def _write_interlock(self, busy: bool) -> None:
        if busy:
            self.new_action.setEnabled(False)
            for button in self.findChildren(QPushButton):
                if button.property("writeAction"):
                    button.setEnabled(False)
        else:
            self.new_action.setEnabled(True)
            self.status_page.set_write_busy(False)
            self.tickets_page.set_write_busy(False)
            self.settings_page.set_write_busy(False)
            self.setup_page.set_global_write_busy(False)
            self.results_page.set_write_busy(False)

    def _settings_loaded(self, settings: SettingsData) -> None:
        self.tickets_page.apply_visible_columns(settings.visible_columns)

    def _settings_saved(self, settings: SettingsData) -> None:
        self._settings_loaded(settings)
        self.load_startup()

    def _new_ticket(self) -> None:
        if self.tasks.write_busy:
            self.statusBar().showMessage("Während einer laufenden Verarbeitung kann kein neuer Entwurf geöffnet werden.", 4500)
            return
        self.navigate("tickets")
        self.tickets_page.new_ticket()

    def _show_results(self, results: tuple[RowResult, ...]) -> None:
        self.results_page.add_results(results)
        self.navigate("results")

    def _open_reloaded_draft(self, draft: object) -> None:
        self.navigate("tickets")
        if isinstance(draft, TicketDraft):
            self.tickets_page.open_draft(draft)

    def _restore_geometry(self) -> None:
        settings = QSettings()
        geometry = settings.value("window/geometry", QByteArray())
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.tasks.busy:
            operation = "eine schreibende Verarbeitung" if self.tasks.write_busy else "ein Ladevorgang"
            QMessageBox.information(
                self,
                "Vorgang läuft",
                f"Aktuell läuft {operation}. Bitte warten Sie bis zum sicheren Abschluss, bevor Sie TicketPilot schließen.",
            )
            event.ignore()
            return
        QSettings().setValue("window/geometry", self.saveGeometry())
        event.accept()

    def prepare_shutdown(self, timeout_ms: int = 60_000) -> bool:
        """Drain background work before the composition root closes SQLite."""

        return self.tasks.shutdown(timeout_ms)
