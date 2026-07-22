"""Shared observable presentation state."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, Signal

from .contracts import StartupSnapshot


class UiSession(QObject):
    changed = Signal()
    safety_changed = Signal(bool, str)
    connection_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.user_display_name = "Nicht verbunden"
        self.user_account = ""
        self.project_key = "DAH"
        self.project_name = "Data & Analytics Hub"
        self.jira_url = "https://jira.dfd-hamburg.de"
        self.dry_run = True
        self.online = False
        self.cache_updated_at: datetime | None = None
        self.cache_valid_until: datetime | None = None
        self.board_name = "Nicht ausgewählt"
        self.pending_drafts = 0
        self.conflicts = 0
        self.last_run_at: datetime | None = None
        self.last_run_summary = "Noch keine Verarbeitung"

    def update_from(self, snapshot: StartupSnapshot) -> None:
        old_safety = (self.dry_run, self.project_key)
        old_online = self.online
        for name in StartupSnapshot.__dataclass_fields__:
            setattr(self, name, getattr(snapshot, name))
        if old_safety != (self.dry_run, self.project_key):
            self.safety_changed.emit(self.dry_run, self.project_key)
        if old_online != self.online:
            self.connection_changed.emit(self.online)
        self.changed.emit()
