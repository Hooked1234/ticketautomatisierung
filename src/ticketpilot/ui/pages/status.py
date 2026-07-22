"""Start and runtime status page."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..components import MetricCard, NoticeBanner, PageHeader, StatusBadge, format_datetime
from ..session import UiSession


class StatusPage(QWidget):
    new_ticket_requested = Signal()
    metadata_refresh_requested = Signal()
    setup_requested = Signal()

    def __init__(self, session: UiSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = session
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(PageHeader("Status", "Persönlicher Arbeitsbereich für sichere Jira-Ticketverarbeitung."))
        self.connection_banner = NoticeBanner()
        layout.addWidget(self.connection_banner)
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        self.user_card = MetricCard("Angemeldeter Nutzer")
        self.project_card = MetricCard("Zielprojekt")
        self.cache_card = MetricCard("Metadaten-Cache")
        self.draft_card = MetricCard("Lokale Entwürfe")
        for index, card in enumerate((self.user_card, self.project_card, self.cache_card, self.draft_card)):
            metrics.addWidget(card, index // 2, index % 2)
        layout.addLayout(metrics)

        run_card = QFrame()
        run_card.setProperty("card", True)
        run_layout = QVBoxLayout(run_card)
        heading = QHBoxLayout()
        title = QLabel("Letzte Verarbeitung")
        title.setProperty("role", "sectionTitle")
        self.run_badge = StatusBadge("Noch keine", "info")
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(self.run_badge)
        self.run_detail = QLabel()
        self.run_detail.setWordWrap(True)
        self.run_detail.setProperty("role", "muted")
        run_layout.addLayout(heading)
        run_layout.addWidget(self.run_detail)
        layout.addWidget(run_card)

        actions = QHBoxLayout()
        self.new_button = QPushButton("&Neues Ticket")
        self.new_button.setProperty("primary", True)
        self.new_button.setProperty("writeAction", True)
        self.metadata_button = QPushButton("Metadaten &aktualisieren")
        self.setup_button = QPushButton("Einrichtung öffnen")
        self.new_button.clicked.connect(self.new_ticket_requested)
        self.metadata_button.clicked.connect(self.metadata_refresh_requested)
        self.setup_button.clicked.connect(self.setup_requested)
        actions.addWidget(self.new_button)
        actions.addWidget(self.metadata_button)
        actions.addWidget(self.setup_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        self._session.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        session = self._session
        if session.online:
            self.connection_banner.set_notice(
                f"Read-only verbunden mit {session.jira_url} als {session.user_display_name}.", "success"
            )
        else:
            self.connection_banner.set_notice(
                "Nicht mit Jira verbunden. Lokale Entwürfe bleiben verfügbar; Remote-Daten sind deaktiviert.", "warning"
            )
        self.user_card.set_data("Angemeldeter Nutzer", session.user_display_name, session.user_account or "Keine Kontokennung")
        self.project_card.set_data("Zielprojekt", session.project_key, session.project_name)
        cache_value = "Aktuell" if session.cache_valid_until else "Nicht geladen"
        cache_hint = f"Stand: {format_datetime(session.cache_updated_at)}"
        self.cache_card.set_data("Metadaten-Cache", cache_value, cache_hint)
        conflict_hint = f"{session.conflicts} Konflikt(e)" if session.conflicts else "Keine offenen Konflikte"
        self.draft_card.set_data("Lokale Entwürfe", str(session.pending_drafts), conflict_hint)
        self.run_badge.setText("Dry Run" if session.dry_run else "Echte Ausführung")
        self.run_badge.set_tone("warning" if session.dry_run else "danger")
        self.run_detail.setText(f"{session.last_run_summary} · {format_datetime(session.last_run_at)}")

    def set_write_busy(self, busy: bool) -> None:
        self.new_button.setEnabled(not busy)
