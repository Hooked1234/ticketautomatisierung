"""Qt item models for the desktop views."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from .components import format_datetime
from .contracts import (
    AuditItem,
    CommentItem,
    ConflictItem,
    RelatedItemResult,
    RowResult,
    TicketListItem,
)
from .theme import COLORS

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Column(Generic[T]):
    title: str
    value: Any
    accessible: str = ""
    alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


class RecordTableModel(QAbstractTableModel, Generic[T]):
    columns: tuple[Column[T], ...] = ()

    def __init__(self, records: Sequence[T] = (), parent: Any = None) -> None:
        super().__init__(parent)
        self._records = list(records)

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
    ) -> int:
        return 0 if parent.isValid() else len(self._records)

    def columnCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),
    ) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section].title
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._records)):
            return None
        record = self._records[index.row()]
        column = self.columns[index.column()]
        value = column.value(record) if callable(column.value) else getattr(record, column.value)
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            return value
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return column.alignment
        if role == Qt.ItemDataRole.UserRole:
            return record
        if role == Qt.ItemDataRole.AccessibleTextRole:
            name = column.accessible or column.title
            return f"{name}: {value}"
        return self.style_role(record, role)

    def style_role(self, record: T, role: int) -> Any:
        return None

    def set_records(self, records: Sequence[T]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()

    def record(self, row: int) -> T | None:
        return self._records[row] if 0 <= row < len(self._records) else None

    def records(self) -> tuple[T, ...]:
        return tuple(self._records)


class TicketTableModel(RecordTableModel[TicketListItem]):
    columns = (
        Column("Aktion", "action"),
        Column("Projekt", "project"),
        Column("Vorgangstyp", "issue_type"),
        Column("Zusammenfassung", "summary"),
        Column("Jira Key", lambda item: item.jira_key or "—"),
        Column("Ergebnis", "result"),
        Column("Geändert", lambda item: format_datetime(item.changed_at, "—")),
    )

    def style_role(self, record: TicketListItem, role: int) -> Any:
        unsafe_result = any(
            marker in record.result.casefold() for marker in ("fehler", "konflikt", "unklar")
        )
        if role == Qt.ItemDataRole.ForegroundRole and (record.has_conflict or unsafe_result):
            return QColor(COLORS["danger"])
        if role == Qt.ItemDataRole.FontRole and (record.has_conflict or unsafe_result):
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.ToolTipRole and (record.has_conflict or unsafe_result):
            return "Nicht erfolgreich verarbeitet. Details unter Ergebnisse & Konflikte prüfen."
        return None


class ResultTableModel(RecordTableModel[RowResult]):
    columns = (
        Column("Zeit", lambda item: format_datetime(item.timestamp, "—")),
        Column("Aktion", "action"),
        Column("Zusammenfassung", "summary"),
        Column("Status", "status"),
        Column("Jira Key", lambda item: item.jira_key or "—"),
        Column("Anhänge / Links", lambda item: _related_summary(item)),
        Column("Meldung", "message"),
    )

    def style_role(self, record: RowResult, role: int) -> Any:
        status = record.status.casefold()
        if role == Qt.ItemDataRole.ForegroundRole:
            if (
                record.conflict
                or record.has_uncertain_state
                or "fehler" in status
                or "unsicher" in status
                or "unklar" in status
            ):
                return QColor(COLORS["danger"])
            if record.has_partial_failure:
                return QColor(COLORS["warning"])
            if "erfolg" in status or "erstellt" in status or "aktualisiert" in status:
                return QColor(COLORS["success"])
            if "dry" in status or "ignor" in status or "verhindert" in status:
                return QColor(COLORS["warning"])
        if role == Qt.ItemDataRole.ToolTipRole:
            if record.has_uncertain_state:
                return "Mindestens eine Teiloperation hat einen unklaren Ausgang. Nicht automatisch wiederholen."
            if record.has_partial_failure:
                return "Nur ein Teil der Anhänge oder Issue Links wurde erfolgreich verarbeitet."
        return None


def _related_summary(record: RowResult) -> str:
    if not record.related:
        return "—"
    successful = sum(item.outcome == "SUCCESS" for item in record.related)
    if record.has_uncertain_state:
        return f"Unklar · {successful}/{len(record.related)} erfolgreich"
    if record.has_partial_failure:
        return f"Teilerfolg · {successful}/{len(record.related)} erfolgreich"
    if successful == len(record.related):
        return f"{successful}/{len(record.related)} erfolgreich"
    return f"0/{len(record.related)} erfolgreich"


class RelatedResultTableModel(RecordTableModel[RelatedItemResult]):
    columns = (
        Column("Typ", lambda item: _related_kind_label(item.kind)),
        Column("Referenz", "reference"),
        Column("Ergebnis", lambda item: _related_outcome_label(item.outcome)),
        Column("Bereinigte Meldung", "message"),
    )

    def style_role(self, record: RelatedItemResult, role: int) -> Any:
        if role == Qt.ItemDataRole.ForegroundRole:
            if record.outcome == "SUCCESS":
                return QColor(COLORS["success"])
            if record.outcome in {"FAILED", "UNCERTAIN"}:
                return QColor(COLORS["danger"])
        if role == Qt.ItemDataRole.FontRole and record.outcome == "UNCERTAIN":
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.ToolTipRole and record.outcome == "UNCERTAIN":
            return "Ausgang unklar: nicht automatisch wiederholen."
        return None


def _related_kind_label(kind: str) -> str:
    return {"attachment": "Anhang", "link": "Issue Link"}.get(kind.casefold(), kind)


def _related_outcome_label(outcome: str) -> str:
    return {
        "SUCCESS": "Erfolgreich",
        "FAILED": "Definitiver Fehler",
        "UNCERTAIN": "Unklar",
    }.get(outcome, outcome)


class ConflictTableModel(RecordTableModel[ConflictItem]):
    columns = (
        Column("Erkannt", lambda item: format_datetime(item.detected_at, "—")),
        Column("Jira Key", "jira_key"),
        Column("Zusammenfassung", "summary"),
        Column("Feld", "field"),
        Column("Lokaler Wert", "local_value"),
        Column("Jira-Wert", "remote_value"),
    )

    def style_role(self, record: ConflictItem, role: int) -> Any:
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(COLORS["danger"])
        if role == Qt.ItemDataRole.ToolTipRole:
            return record.guidance
        return None


class CommentTableModel(RecordTableModel[CommentItem]):
    columns = (
        Column("Neu", lambda item: "Neu" if item.is_new else ""),
        Column("Jira Key", "issue_key"),
        Column("Ticket", "issue_summary"),
        Column("Autor", "author"),
        Column("Erstellt", lambda item: format_datetime(item.created_at, "—")),
        Column("Geändert", lambda item: format_datetime(item.updated_at, "—")),
    )

    def style_role(self, record: CommentItem, role: int) -> Any:
        if record.is_new and role == Qt.ItemDataRole.FontRole:
            font = QFont()
            font.setBold(True)
            return font
        if record.is_new and role == Qt.ItemDataRole.ForegroundRole:
            return QColor(COLORS["primary"])
        return None


class AuditTableModel(RecordTableModel[AuditItem]):
    columns = (
        Column("Zeit", lambda item: format_datetime(item.timestamp, "—")),
        Column("Vorgang", "operation"),
        Column("Ziel", "target"),
        Column("Ergebnis", "outcome"),
        Column("Modus", lambda item: "Dry Run" if item.dry_run else "Echt"),
        Column("Details", "detail"),
    )
