"""Read-only reporting dashboard with complete filter surface."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..components import (
    DistributionChart,
    MetricCard,
    NoticeBanner,
    PageHeader,
    StatusBadge,
    format_datetime,
)
from ..contracts import DashboardData, DashboardFilter, SelectOption, TicketPilotFacade
from ..workers import TaskRunner

_FILTER_FIELDS = (
    ("project", "Projekt"),
    ("sprint", "Sprint"),
    ("status", "Status"),
    ("status_category", "Statuskategorie"),
    ("issue_type", "Vorgangstyp"),
    ("assignee", "Zuständig"),
    ("reporter", "Reporter"),
    ("priority", "Priorität"),
    ("component", "Komponente"),
    ("team", "Team"),
    ("epic", "Epic"),
    ("label", "Label"),
    ("impediment", "Impediment"),
)


class DashboardPage(QWidget):
    def __init__(self, facade: TicketPilotFacade, tasks: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._facade = facade
        self._tasks = tasks
        self._load_key = "dashboard:load"
        self._options_key = "dashboard:options"
        self._export_key = "dashboard:export"
        self._filters: dict[str, QComboBox] = {}
        self._metric_cards: list[MetricCard] = []
        self._charts: dict[str, DistributionChart] = {}
        self._initial_load_requested = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 24)
        heading = QHBoxLayout()
        heading.addWidget(PageHeader("Dashboard", "Read-only-Auswertung aller für den Nutzer zugänglichen Projekttickets."), 1)
        heading.addWidget(StatusBadge("NUR LESEN", "success"), 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(heading)
        self.notice = NoticeBanner("Das Dashboard führt ausschließlich lesende JQL-Abfragen aus.", "info")
        outer.addWidget(self.notice)

        quick = QHBoxLayout()
        self.date_from = self._date_edit(None)
        self.date_to = self._date_edit(None)
        self.text_filter = QLineEdit()
        self.text_filter.setPlaceholderText("Jira Key oder Freitext")
        self.apply_button = QPushButton("&Auswertung laden")
        self.apply_button.setProperty("primary", True)
        self.export_button = QPushButton("Als &CSV exportieren…")
        from_label = QLabel("&Von")
        from_label.setBuddy(self.date_from)
        to_label = QLabel("&Bis")
        to_label.setBuddy(self.date_to)
        quick.addWidget(from_label)
        quick.addWidget(self.date_from)
        quick.addWidget(to_label)
        quick.addWidget(self.date_to)
        quick.addWidget(self.text_filter, 1)
        quick.addWidget(self.apply_button)
        quick.addWidget(self.export_button)
        outer.addLayout(quick)
        self.filter_summary = QLabel("Standardfilter werden geladen…")
        self.filter_summary.setProperty("role", "muted")
        outer.addWidget(self.filter_summary)

        self.advanced_group = QGroupBox("Erweiterte Filter")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_layout = QVBoxLayout(self.advanced_group)
        advanced_layout.setContentsMargins(12, 8, 12, 12)
        self.advanced_content = QWidget()
        advanced_grid = QGridLayout(self.advanced_content)
        advanced_grid.setContentsMargins(0, 0, 0, 0)
        advanced_grid.setHorizontalSpacing(12)
        advanced_grid.setVerticalSpacing(7)
        advanced_fields: list[tuple[str, QWidget]] = []
        for key, label in _FILTER_FIELDS:
            combo = QComboBox()
            combo.setEditable(key in {"assignee", "reporter", "epic", "label", "team"})
            combo.addItem("Alle", "")
            self._filters[key] = combo
            advanced_fields.append((label, combo))
        self.jira_key = QLineEdit()
        self.jira_key.setPlaceholderText("z. B. DAH-123")
        advanced_fields.append(("Jira Key", self.jira_key))
        rows_per_column = (len(advanced_fields) + 1) // 2
        for index, (label_text, editor) in enumerate(advanced_fields):
            block = index // rows_per_column
            row = index % rows_per_column
            label_widget = QLabel(label_text)
            label_widget.setBuddy(editor)
            advanced_grid.addWidget(label_widget, row, block * 2)
            advanced_grid.addWidget(editor, row, block * 2 + 1)
            advanced_grid.setColumnStretch(block * 2 + 1, 1)
        advanced_layout.addWidget(self.advanced_content)
        self.advanced_content.setVisible(False)
        self.advanced_group.setMaximumHeight(42)
        self.advanced_group.toggled.connect(self._toggle_advanced)
        outer.addWidget(self.advanced_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        self.metric_grid = QGridLayout()
        for index in range(12):
            card = MetricCard("Kennzahl", "—")
            self._metric_cards.append(card)
            self.metric_grid.addWidget(card, index // 4, index % 4)
        content_layout.addLayout(self.metric_grid)
        self.chart_tabs = QTabWidget()
        for key, title in (
            ("issue_type", "Vorgangstyp"),
            ("priority", "Priorität"),
            ("component", "Komponente"),
            ("team", "Team"),
            ("sprint", "Sprint"),
            ("story_points", "Story Points"),
        ):
            chart = DistributionChart(f"Verteilung nach {title}")
            self._charts[key] = chart
            self.chart_tabs.addTab(chart, title)
        content_layout.addWidget(self.chart_tabs)
        self.generated_label = QLabel("Noch nicht geladen")
        self.generated_label.setProperty("role", "muted")
        content_layout.addWidget(self.generated_label)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.apply_button.clicked.connect(self.load)
        self.export_button.clicked.connect(self.export_csv)
        self.text_filter.returnPressed.connect(self.load)
        self._tasks.task_succeeded.connect(self._success)
        self._tasks.task_failed.connect(self._failure)
        self._tasks.task_finished.connect(self._finished)
        self.date_from.dateChanged.connect(self._update_filter_summary)
        self.date_to.dateChanged.connect(self._update_filter_summary)
        self._filters["sprint"].currentIndexChanged.connect(self._update_filter_summary)
        self.load_options()

    @staticmethod
    def _date_edit(value: date | None) -> QDateEdit:
        widget = QDateEdit()
        widget.setMinimumDate(QDate(2000, 1, 1))
        widget.setSpecialValueText("Nicht begrenzt")
        widget.setDate(
            QDate(value.year, value.month, value.day) if value is not None else widget.minimumDate()
        )
        widget.setCalendarPopup(True)
        widget.setDisplayFormat("dd.MM.yyyy")
        return widget

    @staticmethod
    def _date_value(widget: QDateEdit) -> date | None:
        value = widget.date()
        return None if value == widget.minimumDate() else date(value.year(), value.month(), value.day())

    def _toggle_advanced(self, checked: bool) -> None:
        self.advanced_content.setVisible(checked)
        self.advanced_group.setMaximumHeight(16_777_215 if checked else 42)
        self.advanced_group.updateGeometry()

    def _update_filter_summary(self) -> None:
        sprint = self._filters["sprint"].currentText() or "Alle"
        date_from = self._date_value(self.date_from)
        date_to = self._date_value(self.date_to)
        if date_from is None and date_to is None:
            period = "Zeitraum nicht begrenzt"
        else:
            start = date_from.strftime("%d.%m.%Y") if date_from else "offen"
            end = date_to.strftime("%d.%m.%Y") if date_to else "offen"
            period = f"Zeitraum {start}–{end}"
        self.filter_summary.setText(f"Aktiver Filter: Sprint {sprint} · {period}")

    def load_options(self) -> None:
        self._tasks.submit(
            self._options_key,
            lambda token, _progress: {} if token.cancelled else self._facade.dashboard_options(),
            cancellable=True,
        )

    def filters(self) -> DashboardFilter:
        values = {key: str(combo.currentData() or combo.currentText()).replace("Alle", "") for key, combo in self._filters.items()}
        return DashboardFilter(
            project=values["project"] or "DAH",
            sprint=values["sprint"],
            date_from=self._date_value(self.date_from),
            date_to=self._date_value(self.date_to),
            status=values["status"],
            status_category=values["status_category"],
            issue_type=values["issue_type"],
            assignee=values["assignee"],
            reporter=values["reporter"],
            priority=values["priority"],
            component=values["component"],
            team=values["team"],
            epic=values["epic"],
            label=values["label"],
            impediment=values["impediment"],
            jira_key=self.jira_key.text().strip(),
            text=self.text_filter.text().strip(),
        )

    def load(self) -> None:
        filters = self.filters()
        if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
            self.notice.set_notice("Der Von-Zeitraum darf nicht nach dem Bis-Datum liegen.", "danger")
            self.date_from.setFocus()
            return
        if self._tasks.submit(
            self._load_key,
            lambda token, _progress: DashboardData() if token.cancelled else self._facade.load_dashboard(filters),
            cancellable=True,
        ):
            self.apply_button.setEnabled(False)
            self.notice.set_notice("Read-only-Auswertung wird geladen…", "info")

    def export_csv(self) -> None:
        destination, _filter = QFileDialog.getSaveFileName(self, "Dashboard als CSV exportieren", "dashboard.csv", "CSV (*.csv)")
        if not destination:
            return
        filters = self.filters()
        self._tasks.submit(
            self._export_key,
            lambda _token, _progress: self._facade.export_dashboard_csv(filters, Path(destination)),
            cancellable=False,
        )

    def _success(self, key: str, payload: object) -> None:
        if key == self._options_key and isinstance(payload, Mapping):
            for filter_key, combo in self._filters.items():
                options = payload.get(filter_key, ())
                current = str(combo.currentData() or "")
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("Alle", "")
                for option in options:
                    if isinstance(option, SelectOption):
                        combo.addItem(option.label, option.value)
                        combo.setItemData(
                            combo.count() - 1,
                            option.description,
                            Qt.ItemDataRole.ToolTipRole,
                        )
                index = combo.findData(current)
                if filter_key == "sprint" and not current:
                    index = next(
                        (
                            option_index
                            for option_index, option in enumerate(options, start=1)
                            if isinstance(option, SelectOption)
                            and option.enabled
                            and option.description.casefold() == "active"
                        ),
                        index,
                    )
                combo.setCurrentIndex(max(0, index))
                combo.blockSignals(False)
            self._update_filter_summary()
            if not self._initial_load_requested:
                self._initial_load_requested = True
                QTimer.singleShot(0, self.load)
        elif key == self._load_key and isinstance(payload, DashboardData):
            self._render(payload)
        elif key == self._export_key and isinstance(payload, (Path, str)):
            self.notice.set_notice(f"CSV wurde gespeichert: {payload}", "success")

    def _render(self, data: DashboardData) -> None:
        for index, card in enumerate(self._metric_cards):
            if index < len(data.metrics):
                metric = data.metrics[index]
                card.set_data(metric.label, metric.value, metric.hint)
                card.show()
            else:
                card.hide()
        for key, chart in self._charts.items():
            chart.set_items(data.breakdowns.get(key, ()))
        self.generated_label.setText(
            f"{data.scope_description} · {data.result_count} Ticket(s) · Stand {format_datetime(data.generated_at)}"
        )
        self.notice.set_notice("Read-only-Auswertung erfolgreich aktualisiert.", "success")

    def _failure(self, key: str, message: str, _detail: str) -> None:
        if key in {self._load_key, self._options_key, self._export_key}:
            self.notice.set_notice(f"Dashboard-Aktion fehlgeschlagen: {message}", "danger")

    def _finished(self, key: str) -> None:
        if key == self._load_key:
            self.apply_button.setEnabled(True)
