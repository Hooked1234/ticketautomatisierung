"""Read-only Jira comment overview without AI processing."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..components import NoticeBanner, PageHeader, StatusBadge, format_datetime
from ..contracts import CommentFilter, CommentItem, TicketPilotFacade
from ..models import CommentTableModel
from ..workers import TaskRunner


class CommentsPage(QWidget):
    def __init__(self, facade: TicketPilotFacade, tasks: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._facade = facade
        self._tasks = tasks
        self._load_key = "comments:load"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        heading = QHBoxLayout()
        heading.addWidget(PageHeader("Kommentare", "Read-only-Übersicht; keine KI-Zusammenfassung und keine externe Verarbeitung."), 1)
        heading.addWidget(StatusBadge("NUR LESEN", "success"), 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading)
        self.notice = NoticeBanner()
        layout.addWidget(self.notice)
        filters = QHBoxLayout()
        self.issue_key = QLineEdit()
        self.issue_key.setPlaceholderText("Jira Key")
        self.author = QLineEdit()
        self.author.setPlaceholderText("Autor")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Kommentartext durchsuchen")
        self.only_new = QCheckBox("Nur neue seit letzter Prüfung")
        self.load_button = QPushButton("&Kommentare laden")
        filters.addWidget(self.issue_key)
        filters.addWidget(self.author)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.only_new)
        filters.addWidget(self.load_button)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.model = CommentTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName("Vollständiger Kommentartext")
        self.detail.setPlaceholderText("Kommentar auswählen, um den vollständigen Text zu lesen.")
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.summary = QLabel("Noch nicht geladen")
        self.summary.setProperty("role", "muted")
        layout.addWidget(self.summary)

        self.load_button.clicked.connect(self.load)
        self.search.returnPressed.connect(self.load)
        self.table.selectionModel().currentRowChanged.connect(self._show_comment)
        self._tasks.task_succeeded.connect(self._success)
        self._tasks.task_failed.connect(self._failure)
        self._tasks.task_finished.connect(self._finished)

    def filters(self) -> CommentFilter:
        return CommentFilter(
            text=self.search.text().strip(),
            issue_key=self.issue_key.text().strip(),
            author=self.author.text().strip(),
            only_new=self.only_new.isChecked(),
        )

    def load(self) -> None:
        filters = self.filters()
        if self._tasks.submit(
            self._load_key,
            lambda token, _progress: () if token.cancelled else self._facade.list_comments(filters),
            cancellable=True,
        ):
            self.load_button.setEnabled(False)
            self.notice.set_notice("Kommentare werden read-only geladen…", "info")

    def _success(self, key: str, payload: object) -> None:
        if key != self._load_key or not isinstance(payload, Sequence):
            return
        comments = tuple(item for item in payload if isinstance(item, CommentItem))
        self.model.set_records(comments)
        self.table.resizeColumnsToContents()
        new_count = sum(item.is_new for item in comments)
        self.summary.setText(f"{len(comments)} Kommentar(e), davon {new_count} neu")
        self.notice.set_notice("Kommentarübersicht aktualisiert.", "success")
        self.detail.clear()
        if comments:
            self.table.selectRow(0)

    def _show_comment(self, current: QModelIndex, _previous: QModelIndex) -> None:
        comment = self.model.record(current.row()) if current.isValid() else None
        if comment is None:
            self.detail.clear()
            return
        changed = f" · geändert {format_datetime(comment.updated_at)}" if comment.updated_at else ""
        self.detail.setPlainText(
            f"{comment.issue_key} — {comment.issue_summary}\n"
            f"{comment.author} · erstellt {format_datetime(comment.created_at)}{changed}\n\n"
            f"{comment.body}"
        )

    def _failure(self, key: str, message: str, _detail: str) -> None:
        if key == self._load_key:
            self.notice.set_notice(f"Kommentare konnten nicht geladen werden: {message}", "danger")

    def _finished(self, key: str) -> None:
        if key == self._load_key:
            self.load_button.setEnabled(True)
