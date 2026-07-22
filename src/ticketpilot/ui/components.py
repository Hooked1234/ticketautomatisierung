"""Reusable, accessible presentation widgets."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from PySide6.QtCore import QEvent, QRectF, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .contracts import BreakdownItem, SelectOption
from .theme import COLORS


def format_datetime(value: datetime | None, fallback: str = "Noch nie") -> str:
    if value is None:
        return fallback
    return value.astimezone().strftime("%d.%m.%Y, %H:%M") if value.tzinfo else value.strftime("%d.%m.%Y, %H:%M")


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setProperty("role", "pageTitle")
        title_label.setAccessibleName(title)
        layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setProperty("role", "muted")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)


class NoticeBanner(QFrame):
    def __init__(self, text: str = "", tone: str = "info", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("notice", True)
        self.setProperty("tone", tone)
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)
        self.hide() if not text else self.show()

    def set_notice(self, text: str, tone: str = "info") -> None:
        self.label.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(bool(text))


class StatusBadge(QLabel):
    def __init__(self, text: str = "", tone: str = "info", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        self.set_tone(tone)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class SafetyStrip(QFrame):
    """Persistent target and dry-run context, never conveyed by color alone."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("notice", True)
        self.setProperty("tone", "warning")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(14)
        self.mode = StatusBadge("DRY RUN AKTIV", "warning")
        self.target = QLabel("Zielprojekt: DAH")
        self.target.setProperty("role", "sectionTitle")
        self.detail = QLabel("Es werden keine Änderungen an Jira gesendet.")
        self.detail.setProperty("role", "muted")
        layout.addWidget(self.mode)
        layout.addWidget(self.target)
        layout.addWidget(self.detail, 1)

    def update_safety(self, dry_run: bool, project: str) -> None:
        self.target.setText(f"Zielprojekt: {project or '—'}")
        if dry_run:
            self.mode.setText("DRY RUN AKTIV")
            self.mode.set_tone("warning")
            self.detail.setText("Es werden keine Änderungen an Jira gesendet.")
            self.setProperty("tone", "warning")
        else:
            self.mode.setText("ECHTE AUSFÜHRUNG")
            self.mode.set_tone("danger")
            self.detail.setText("Bestätigte Aktionen können Jira verändern.")
            self.setProperty("tone", "danger")
        self.style().unpolish(self)
        self.style().polish(self)


class MetricCard(QFrame):
    def __init__(self, label: str = "", value: str = "—", hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self.setMinimumWidth(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self.label = QLabel(label)
        self.label.setProperty("role", "muted")
        self.value = QLabel(value)
        self.value.setProperty("role", "metric")
        self.hint = QLabel(hint)
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set_data(self, label: str, value: str, hint: str = "") -> None:
        self.label.setText(label)
        self.value.setText(value)
        self.hint.setText(hint)
        self.setAccessibleName(f"{label}: {value}. {hint}".strip())


class EmptyState(QWidget):
    def __init__(self, title: str, detail: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading = QLabel(title)
        heading.setProperty("role", "sectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(detail)
        text.setProperty("role", "muted")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        layout.addWidget(text)


class TagEditor(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Wert eingeben und Enter drücken")
        self.add_button = QPushButton("Hinzufügen")
        self.add_button.setAutoDefault(False)
        row.addWidget(self.input, 1)
        row.addWidget(self.add_button)
        self.list = QListWidget()
        self.list.setMaximumHeight(94)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAccessibleName("Ausgewählte Werte")
        layout.addLayout(row)
        layout.addWidget(self.list)
        self.input.returnPressed.connect(self._add)
        self.add_button.clicked.connect(self._add)
        self.list.installEventFilter(self)
        self._completion_model = QStringListModel(self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.input.setCompleter(self._completer)

    def _add(self) -> None:
        value = self.input.text().strip()
        if value and value.casefold() not in {item.casefold() for item in self.values()}:
            self.list.addItem(value)
            self.input.clear()
            self.changed.emit()

    def values(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def set_values(self, values: Iterable[str]) -> None:
        self.list.clear()
        for value in values:
            if str(value).strip():
                self.list.addItem(str(value).strip())
        self.changed.emit()

    def set_suggestions(self, values: Iterable[str]) -> None:
        self._completion_model.setStringList(sorted({str(value) for value in values if str(value).strip()}, key=str.casefold))

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        if watched is self.list and event.type() == QEvent.Type.KeyPress:
            key_event = event if isinstance(event, QKeyEvent) else None
            if key_event and key_event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
                for item in self.list.selectedItems():
                    self.list.takeItem(self.list.row(item))
                self.changed.emit()
                return True
        return super().eventFilter(watched, event)


class MultiSelectList(QListWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMaximumHeight(118)
        self.itemChanged.connect(lambda _item: self.changed.emit())

    def set_options(self, options: Sequence[SelectOption], selected: Iterable[str] = ()) -> None:
        chosen = set(selected)
        self.blockSignals(True)
        self.clear()
        for option in options:
            item = QListWidgetItem(option.label)
            item.setData(Qt.ItemDataRole.UserRole, option.value)
            item.setToolTip(option.description)
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            if not option.enabled:
                flags &= ~Qt.ItemFlag.ItemIsEnabled
            item.setFlags(flags)
            item.setCheckState(Qt.CheckState.Checked if option.value in chosen else Qt.CheckState.Unchecked)
            self.addItem(item)
        self.blockSignals(False)

    def values(self) -> list[str]:
        return [
            str(self.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.count())
            if self.item(i).checkState() == Qt.CheckState.Checked
        ]


class SearchField(QWidget):
    search_requested = Signal()
    value_changed = Signal(str)

    def __init__(self, button_text: str = "Suchen…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.input = QLineEdit()
        self.button = QPushButton(button_text)
        self.button.setAutoDefault(False)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.button)
        self.button.clicked.connect(self.search_requested)
        self.input.textEdited.connect(self._manual_edit)

    def value(self) -> str:
        return str(self.input.property("selectedValue") or "")

    def set_value(self, value: str, label: str | None = None) -> None:
        self.input.setProperty("selectedValue", value)
        self.input.setText(label or value)
        self.value_changed.emit(value)

    def clear_selection(self) -> None:
        self.input.setProperty("selectedValue", "")
        self.input.clear()
        self.value_changed.emit("")

    def _manual_edit(self, _text: str) -> None:
        self.input.setProperty("selectedValue", "")
        self.value_changed.emit("")


class AssigneeField(QWidget):
    """Explicit safe assignee modes plus assignable-user search."""

    search_requested = Signal()
    value_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.addItem("Unassigned", "")
        self.mode.addItem("Assign to me", "@me")
        self.mode.addItem("Zuweisbare Person suchen…", "search")
        self.person = SearchField("Person suchen…")
        self.person.hide()
        layout.addWidget(self.mode)
        layout.addWidget(self.person)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.person.search_requested.connect(self.search_requested)
        self.person.value_changed.connect(self.value_changed)

    def _mode_changed(self) -> None:
        is_search = self.mode.currentData() == "search"
        self.person.setVisible(is_search)
        self.value_changed.emit(self.value())
        if is_search:
            self.person.input.setFocus()

    def value(self) -> str:
        return self.person.value() if self.mode.currentData() == "search" else str(self.mode.currentData() or "")

    def set_value(self, value: str, label: str | None = None) -> None:
        if value in {"", "@me"}:
            self.mode.setCurrentIndex(self.mode.findData(value))
        else:
            self.mode.setCurrentIndex(self.mode.findData("search"))
            self.person.set_value(value, label)


class SearchMultiField(QWidget):
    search_requested = Signal()
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.search_text = QLineEdit()
        self.search_text.setPlaceholderText("Suchen und hinzufügen")
        self.search_button = QPushButton("Suchen…")
        self.remove_button = QPushButton("Auswahl entfernen")
        self.remove_button.setAutoDefault(False)
        row.addWidget(self.search_text, 1)
        row.addWidget(self.search_button)
        self.values_list = QListWidget()
        self.values_list.setMaximumHeight(96)
        self.values_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.values_list.setAccessibleName("Ausgewählte Suchwerte")
        layout.addLayout(row)
        layout.addWidget(self.values_list)
        layout.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignRight)
        self.search_button.clicked.connect(self.search_requested)
        self.search_text.returnPressed.connect(self.search_requested)
        self.remove_button.clicked.connect(self._remove_selected)

    def add_value(self, value: str, label: str | None = None) -> None:
        if value in self.values():
            return
        item = QListWidgetItem(label or value)
        item.setData(Qt.ItemDataRole.UserRole, value)
        self.values_list.addItem(item)
        self.search_text.clear()
        self.changed.emit()

    def values(self) -> list[str]:
        return [str(self.values_list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.values_list.count())]

    def set_values(self, values: Iterable[str]) -> None:
        self.values_list.clear()
        for value in values:
            self.add_value(str(value), str(value))

    def _remove_selected(self) -> None:
        for item in self.values_list.selectedItems():
            self.values_list.takeItem(self.values_list.row(item))
        self.changed.emit()


class ClearableField(QWidget):
    """Makes UPDATE's explicit ``<CLEAR>`` state distinct from an empty value."""

    def __init__(self, editor: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor = editor
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.clear_value = QCheckBox("Vorhandenen Wert entfernen (<CLEAR>)")
        self.clear_value.setToolTip("Nur diese Auswahl entfernt den vorhandenen optionalen Jira-Wert.")
        layout.addWidget(editor)
        layout.addWidget(self.clear_value)
        self.clear_value.toggled.connect(lambda checked: self.editor.setEnabled(not checked))

    def is_clear(self) -> bool:
        return self.clear_value.isChecked()

    def set_clear(self, clear: bool) -> None:
        self.clear_value.setChecked(clear)


class DistributionChart(QWidget):
    """Keyboard-readable compact horizontal bar chart."""

    def __init__(self, title: str = "Verteilung", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._items: list[BreakdownItem] = []
        self._selected = 0
        self.setMinimumHeight(220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setToolTip("Mit Pfeiltasten einzelne Werte auswählen.")

    def sizeHint(self) -> QSize:
        return QSize(500, max(220, 50 + 30 * len(self._items)))

    def set_items(self, items: Sequence[BreakdownItem]) -> None:
        self._items = list(items)
        self._selected = min(self._selected, max(0, len(self._items) - 1))
        summary = "; ".join(
            f"{item.label}: {item.formatted_value or f'{item.value:g}'}" for item in self._items
        )
        self.setAccessibleDescription(summary or "Keine Daten")
        self.updateGeometry()
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Down, Qt.Key.Key_Right} and self._items:
            self._selected = (self._selected + 1) % len(self._items)
            self.update()
            return
        if event.key() in {Qt.Key.Key_Up, Qt.Key.Key_Left} and self._items:
            self._selected = (self._selected - 1) % len(self._items)
            self.update()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(COLORS["text"]))
        title_font = painter.font()
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(QRectF(8, 6, self.width() - 16, 25), Qt.AlignmentFlag.AlignLeft, self._title)
        if not self._items:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(QRectF(8, 48, self.width() - 16, 40), Qt.AlignmentFlag.AlignLeft, "Keine Daten für diese Filter.")
            return
        normal_font = painter.font()
        normal_font.setBold(False)
        painter.setFont(normal_font)
        max_value = max((item.value for item in self._items), default=1.0) or 1.0
        label_width = min(max(110, self.width() // 3), 230)
        bar_x = label_width + 18
        bar_width = max(60, self.width() - bar_x - 70)
        y = 42
        for index, item in enumerate(self._items):
            selected = index == self._selected and self.hasFocus()
            label_rect = QRectF(8, y, label_width - 4, 22)
            painter.setPen(QColor(COLORS["text"]))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, item.label)
            background = QRectF(bar_x, y + 3, bar_width, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#E4EAF0"))
            painter.drawRoundedRect(background, 3, 3)
            value_width = max(1.0, bar_width * max(0.0, item.value) / max_value)
            path = QPainterPath()
            path.addRoundedRect(QRectF(bar_x, y + 3, value_width, 16), 3, 3)
            painter.fillPath(path, QColor(COLORS["primary"]))
            if selected:
                painter.setPen(QPen(QColor(COLORS["focus"]), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(QRectF(4, y - 2, self.width() - 8, 26), 4, 4)
            painter.setPen(QColor(COLORS["text"]))
            formatted = item.formatted_value or f"{item.value:g}"
            painter.drawText(QRectF(bar_x + bar_width + 8, y, 58, 22), Qt.AlignmentFlag.AlignVCenter, formatted)
            y += 30
