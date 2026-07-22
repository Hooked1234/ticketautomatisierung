"""First-run connection and credential setup."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..components import NoticeBanner, PageHeader
from ..contracts import ConnectionResult, SetupData, TicketPilotFacade
from ..workers import TaskRunner, register_process_secret


class SetupPage(QWidget):
    setup_saved = Signal()

    def __init__(self, facade: TicketPilotFacade, tasks: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._facade = facade
        self._tasks = tasks
        self._test_key = "setup:test-connection"
        self._save_key = "setup:save"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("Einrichtung", "Persönliche Verbindung und sichere lokale Token-Ablage konfigurieren."))
        self.notice = NoticeBanner()
        layout.addWidget(self.notice)

        card = QFrame()
        card.setProperty("card", True)
        form = QFormLayout(card)
        form.setContentsMargins(20, 20, 20, 20)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://jira.example.de")
        self.url.setClearButtonEnabled(True)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Personal Access Token")
        self.token.setAccessibleDescription("Der Token wird nicht angezeigt und nicht in Konfigurationsdateien geschrieben.")
        self.show_token = QCheckBox("Token anzeigen")
        self.show_token.toggled.connect(
            lambda visible: self.token.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        )
        self.remember = QCheckBox("Im Windows-Anmeldeinformationsmanager speichern")
        self.remember.setChecked(True)
        self.storage_note = QLabel()
        self.storage_note.setWordWrap(True)
        self.storage_note.setProperty("role", "muted")
        self.project = QLineEdit("DAH")
        self.project.setReadOnly(True)
        self.username = QLineEdit()
        self.username.setReadOnly(True)
        url_label = QLabel("&Jira-URL")
        url_label.setBuddy(self.url)
        token_label = QLabel("&Persönlicher Token")
        token_label.setBuddy(self.token)
        project_label = QLabel("&Startprojekt")
        project_label.setBuddy(self.project)
        user_label = QLabel("Angemeldeter &Nutzer")
        user_label.setBuddy(self.username)
        form.addRow(url_label, self.url)
        form.addRow(token_label, self.token)
        form.addRow("", self.show_token)
        form.addRow("", self.remember)
        form.addRow("", self.storage_note)
        form.addRow(project_label, self.project)
        form.addRow(user_label, self.username)
        layout.addWidget(card)

        note = NoticeBanner(
            "Der Verbindungstest ist ausschließlich lesend. Der Reporter wird später immer aus diesem angemeldeten Nutzer übernommen.",
            "info",
        )
        layout.addWidget(note)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.test_button = QPushButton("Verbindung &testen")
        self.save_button = QPushButton("Einrichtung &speichern")
        self.save_button.setProperty("primary", True)
        self.save_button.setProperty("writeAction", True)
        self.test_button.clicked.connect(self.test_connection)
        self.save_button.clicked.connect(self.save_setup)
        actions.addWidget(self.test_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        layout.addStretch(1)

        self._tasks.task_succeeded.connect(self._success)
        self._tasks.task_failed.connect(self._failure)
        self._tasks.task_finished.connect(self._finished)
        self.load()
        QWidget.setTabOrder(self.url, self.token)
        QWidget.setTabOrder(self.token, self.show_token)
        QWidget.setTabOrder(self.show_token, self.remember)
        QWidget.setTabOrder(self.remember, self.test_button)
        QWidget.setTabOrder(self.test_button, self.save_button)

    def load(self) -> None:
        try:
            setup = self._facade.load_setup()
        except Exception as exc:
            self.notice.set_notice(str(exc), "warning")
            return
        self.url.setText(setup.jira_url)
        self.project.setText(setup.project)
        self.username.setText(setup.username)
        secure_storage = setup.credential_storage_available
        self.remember.setEnabled(secure_storage)
        self.remember.setChecked(setup.remember_token if secure_storage else False)
        self.storage_note.setText(
            "Sichere dauerhafte Ablage ist verfügbar."
            if secure_storage
            else (
                "Windows-Anmeldeinformationsmanager ist nicht verfügbar. "
                "Der Token bleibt nur für diese Sitzung im Arbeitsspeicher."
            )
        )

    def _validate(self) -> bool:
        if not self.url.text().strip().lower().startswith("https://"):
            self.notice.set_notice("Bitte eine gültige HTTPS-Jira-URL eingeben.", "danger")
            self.url.setFocus()
            return False
        if not self.token.text():
            self.notice.set_notice("Bitte den persönlichen Token eingeben.", "danger")
            self.token.setFocus()
            return False
        return True

    def test_connection(self) -> None:
        if not self._validate():
            return
        url, token = self.url.text().strip(), self.token.text()
        register_process_secret(token)
        if self._tasks.submit(
            self._test_key,
            lambda cancel, _progress: ConnectionResult(False, "Abgebrochen")
            if cancel.cancelled
            else self._facade.test_connection(url, token),
            cancellable=True,
        ):
            self._set_busy(True)

    def save_setup(self) -> None:
        if not self._validate():
            return
        setup = SetupData(
            jira_url=self.url.text().strip(),
            username=self.username.text().strip(),
            project=self.project.text().strip(),
            remember_token=self.remember.isChecked(),
            configured=True,
        )
        token = self.token.text()
        register_process_secret(token)
        if self._tasks.submit(
            self._save_key,
            lambda _cancel, _progress: self._facade.save_setup(setup, token),
            write=True,
            cancellable=False,
        ):
            self._set_busy(True)

    def _success(self, key: str, payload: object) -> None:
        if key not in {self._test_key, self._save_key} or not isinstance(payload, ConnectionResult):
            return
        tone = "success" if payload.ok else "danger"
        detail = f" — {payload.detail}" if payload.detail else ""
        self.notice.set_notice(f"{payload.headline}{detail}", tone)
        if payload.username:
            self.username.setText(payload.username)
        if key == self._save_key and payload.ok:
            self.token.clear()
            self.setup_saved.emit()

    def _failure(self, key: str, message: str, _detail: str) -> None:
        if key in {self._test_key, self._save_key}:
            self.notice.set_notice(f"Einrichtung fehlgeschlagen: {message}", "danger")

    def _finished(self, key: str) -> None:
        if key in {self._test_key, self._save_key}:
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.test_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy and not self._tasks.write_busy)
        self.url.setEnabled(not busy)
        self.token.setEnabled(not busy)

    def set_global_write_busy(self, busy: bool) -> None:
        """Apply the process-wide Jira write interlock."""

        if busy:
            self.save_button.setEnabled(False)
        elif not self._tasks.is_running(self._save_key) and not self._tasks.is_running(self._test_key):
            self.save_button.setEnabled(True)
