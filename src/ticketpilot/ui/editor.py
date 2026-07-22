"""Metadata-driven ticket editor for all supported issue types."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .components import (
    AssigneeField,
    ClearableField,
    MultiSelectList,
    NoticeBanner,
    SafetyStrip,
    SearchField,
    SearchMultiField,
    TagEditor,
)
from .contracts import (
    ACTIONS,
    ISSUE_TYPES,
    AttachmentDraft,
    FieldSpec,
    LinkDraft,
    SelectOption,
    TicketDraft,
    TicketPilotFacade,
    default_editor_fields,
)
from .dialogs import SearchDialog
from .session import UiSession
from .workers import TaskRunner

_MAIN_FIELDS = {
    "summary",
    "description",
    "epic_name",
    "priority",
    "components",
    "assignee",
    "labels",
    "products_services",
    "account",
}
_RELATION_FIELDS = {"epic_link"}
_RESERVED_FIELDS = {"jira_key"}


class TicketEditorDialog(QDialog):
    """Collects a draft but delegates validation and preview to the facade."""

    def __init__(
        self,
        facade: TicketPilotFacade,
        tasks: TaskRunner,
        session: UiSession,
        draft: TicketDraft | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ticket bearbeiten" if draft else "Neues Ticket")
        self.setModal(True)
        self.resize(1020, 790)
        self._facade = facade
        self._tasks = tasks
        self._session = session
        self._source = draft or TicketDraft(project=session.project_key)
        self._has_persisted_issue = bool(self._source.jira_key.strip())
        self._field_specs: dict[str, FieldSpec] = {}
        self._field_widgets: dict[str, QWidget] = {}
        self._field_values: dict[str, Any] = dict(self._source.values)
        if self._source.summary:
            self._field_values["summary"] = self._source.summary
        self._attachments = list(self._source.attachments)
        self._links = list(self._source.links)
        self._options_prefix = f"editor-options:{id(self)}"
        self.completion = "cancel"

        outer = QVBoxLayout(self)
        heading = QLabel(self.windowTitle())
        heading.setProperty("role", "pageTitle")
        outer.addWidget(heading)
        self.safety = SafetyStrip()
        self.safety.update_safety(session.dry_run, self._source.project or session.project_key)
        outer.addWidget(self.safety)
        self.update_hint = NoticeBanner()
        outer.addWidget(self.update_hint)

        identity = QFrame()
        identity.setProperty("card", True)
        identity_form = QFormLayout(identity)
        identity_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.action = QComboBox()
        self.action.addItems(("UPDATE", "IGNORE") if self._has_persisted_issue else ACTIONS)
        source_action = self._source.action
        if self._has_persisted_issue and source_action not in {"UPDATE", "IGNORE"}:
            source_action = "UPDATE"
        self.action.setCurrentText(source_action)
        self.project = QLineEdit(self._source.project or session.project_key)
        self.project.setReadOnly(True)
        self.issue_type = QComboBox()
        self.issue_type.addItems(ISSUE_TYPES)
        self.issue_type.setCurrentText(self._source.issue_type)
        self.jira_key = QLineEdit(self._source.jira_key)
        self.jira_key.setPlaceholderText("Für UPDATE erforderlich")
        self.jira_key.setReadOnly(self._has_persisted_issue)
        action_label = QLabel("&Aktion")
        action_label.setBuddy(self.action)
        project_label = QLabel("&Zielprojekt")
        project_label.setBuddy(self.project)
        type_label = QLabel("&Vorgangstyp")
        type_label.setBuddy(self.issue_type)
        key_label = QLabel("Jira &Key")
        key_label.setBuddy(self.jira_key)
        identity_form.addRow(action_label, self.action)
        identity_form.addRow(project_label, self.project)
        identity_form.addRow(type_label, self.issue_type)
        identity_form.addRow(key_label, self.jira_key)
        outer.addWidget(identity)

        self.tabs = QTabWidget()
        self.main_form = self._form_tab("Inhalt")
        self.planning_form = self._form_tab("Planung")
        relations_page = QWidget()
        relations_layout = QVBoxLayout(relations_page)
        self.relation_form = QFormLayout()
        self.relation_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        relations_layout.addLayout(self.relation_form)
        self.board_context = QLabel(f"Board: {session.board_name}")
        self.board_context.setProperty("role", "muted")
        relations_layout.addWidget(self.board_context)
        relations_layout.addWidget(self._attachment_section())
        relations_layout.addWidget(self._link_section())
        relations_layout.addStretch(1)
        relation_scroll = QScrollArea()
        relation_scroll.setWidgetResizable(True)
        relation_scroll.setWidget(relations_page)
        self.tabs.addTab(relation_scroll, "Anhänge und Beziehungen")
        outer.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Abbrechen")
        self.save_button = QPushButton("Entwurf speichern")
        self.preview_button = QPushButton("Vorschau öffnen")
        self.preview_button.setProperty("primary", True)
        cancel.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._save)
        self.preview_button.clicked.connect(self._preview)
        footer.addWidget(cancel)
        footer.addWidget(self.save_button)
        footer.addWidget(self.preview_button)
        outer.addLayout(footer)

        self.action.currentTextChanged.connect(self._identity_changed)
        self.issue_type.currentTextChanged.connect(self._identity_changed)
        self._tasks.task_succeeded.connect(self._task_success)
        self._tasks.task_failed.connect(self._task_failure)
        self._rebuild_fields()
        self._identity_changed()
        self._refresh_attachment_table()
        self._refresh_link_table()
        self._set_focus_order()

    def _form_tab(self, title: str) -> QFormLayout:
        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(16, 16, 16, 16)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(11)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        return form

    def _attachment_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", True)
        layout = QVBoxLayout(frame)
        title = QLabel("Anhänge")
        title.setProperty("role", "sectionTitle")
        detail = QLabel("Anhänge werden erst nach erfolgreicher Erstellung oder eindeutig geladenem Update hochgeladen.")
        detail.setProperty("role", "muted")
        detail.setWordWrap(True)
        self.attachments_table = QTableWidget(0, 3)
        self.attachments_table.setHorizontalHeaderLabels(("Datei", "Größe", "Lokaler Pfad"))
        self.attachments_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.attachments_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.attachments_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.attachments_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        buttons = QHBoxLayout()
        add = QPushButton("Dateien hinzufügen…")
        remove = QPushButton("Auswahl entfernen")
        add.clicked.connect(self._add_attachments)
        remove.clicked.connect(self._remove_attachments)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(self.attachments_table)
        layout.addLayout(buttons)
        return frame

    def _link_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", True)
        layout = QVBoxLayout(frame)
        title = QLabel("Issue Links")
        title.setProperty("role", "sectionTitle")
        self.links_table = QTableWidget(0, 3)
        self.links_table.setHorizontalHeaderLabels(("Link-Typ", "Richtung", "Ticket"))
        self.links_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.links_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.links_table.horizontalHeader().setStretchLastSection(True)
        add_row = QHBoxLayout()
        self.link_type = QComboBox()
        self.link_direction = QComboBox()
        self.link_direction.addItem("ausgehend", "outward")
        self.link_direction.addItem("eingehend", "inward")
        self.link_issue = SearchField("Ticket suchen…")
        self.link_issue.search_requested.connect(lambda: self._search_link_issue())
        add_link = QPushButton("Link hinzufügen")
        remove_link = QPushButton("Auswahl entfernen")
        add_link.clicked.connect(self._add_link)
        remove_link.clicked.connect(self._remove_links)
        add_row.addWidget(self.link_type)
        add_row.addWidget(self.link_direction)
        add_row.addWidget(self.link_issue, 1)
        add_row.addWidget(add_link)
        layout.addWidget(title)
        layout.addWidget(self.links_table)
        layout.addLayout(add_row)
        layout.addWidget(remove_link, 0, Qt.AlignmentFlag.AlignRight)
        return frame

    def _identity_changed(self) -> None:
        update = self.action.currentText() == "UPDATE"
        ignore = self.action.currentText() == "IGNORE"
        show_key = update or self._has_persisted_issue
        self.jira_key.setVisible(show_key)
        parent = self.jira_key.parentWidget()
        parent_layout = parent.layout() if parent is not None else None
        label = (
            parent_layout.labelForField(self.jira_key)
            if isinstance(parent_layout, QFormLayout)
            else None
        )
        if label:
            label.setVisible(show_key)
        self.issue_type.setEnabled(not update and not self._has_persisted_issue)
        self.update_hint.set_notice(
            "Bei UPDATE bleibt ein leeres Feld unverändert. Nur der exakte Wert <CLEAR> entfernt einen optionalen Wert. "
            "Projekt, Vorgangstyp, Reporter, Jira Key und Status sind gesperrt.",
            "info",
        ) if update else self.update_hint.set_notice("", "info")
        self.tabs.setEnabled(not ignore)
        self._rebuild_fields()
        self._load_options()

    def _capture_field_values(self) -> None:
        for key, widget in self._field_widgets.items():
            self._field_values[key] = self._widget_value(widget)

    def _rebuild_fields(self) -> None:
        if self._field_widgets:
            self._capture_field_values()
        for form in (self.main_form, self.planning_form, self.relation_form):
            while form.rowCount():
                form.removeRow(0)
        self._field_widgets.clear()
        issue_type = self.issue_type.currentText() or "Story"
        action = self.action.currentText() or "CREATE"
        try:
            specs = tuple(self._facade.ticket_fields(issue_type, action))
        except Exception:
            specs = default_editor_fields(issue_type, action)
        self._field_specs = {spec.key: spec for spec in specs}
        allowed_fields = set(self._field_specs)
        self._field_values = {
            key: value for key, value in self._field_values.items() if key in allowed_fields
        }
        for spec in specs:
            if spec.key in _RESERVED_FIELDS:
                continue
            widget = self._make_widget(spec)
            self._field_widgets[spec.key] = widget
            label_text = f"{spec.label}{' *' if spec.required else ''}"
            label = QLabel(label_text)
            label.setBuddy(widget.editor if isinstance(widget, ClearableField) else widget)
            if spec.help_text:
                label.setToolTip(spec.help_text)
                widget.setToolTip(spec.help_text)
            target = self.main_form if spec.key in _MAIN_FIELDS else self.relation_form if spec.key in _RELATION_FIELDS else self.planning_form
            target.addRow(label, widget)
            self._set_widget_value(widget, self._field_values.get(spec.key))

    def _make_widget(self, spec: FieldSpec) -> QWidget:
        editor = spec.editor
        widget: QWidget
        if spec.key == "assignee":
            widget = AssigneeField()
            widget.search_requested.connect(lambda _checked=False, key=spec.key: self._search_field(key))
        elif editor == "multiline":
            widget = QTextEdit()
            widget.setMinimumHeight(105)
            widget.setStyleSheet("min-height: 90px;")
            widget.setPlaceholderText(spec.placeholder)
        elif editor == "combo":
            widget = QComboBox()
            widget.setEditable(False)
            for option in spec.choices:
                widget.addItem(option.label, option.value)
        elif editor == "multi-combo":
            widget = MultiSelectList()
            widget.set_options(spec.choices)
        elif editor == "tags":
            widget = TagEditor()
        elif editor == "search":
            widget = SearchField()
            widget.input.setPlaceholderText(spec.placeholder or "Wert suchen")
            widget.search_requested.connect(lambda _checked=False, key=spec.key: self._search_field(key))
        elif editor == "search-multi":
            widget = SearchMultiField()
            widget.search_requested.connect(lambda _checked=False, key=spec.key: self._search_field(key))
        elif editor == "date":
            widget = QDateEdit()
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("dd.MM.yy")
            widget.setMinimumDate(QDate(2000, 1, 1))
            widget.setSpecialValueText("Nicht gesetzt")
            widget.setDate(widget.minimumDate())
        elif editor == "number":
            widget = QDoubleSpinBox()
            widget.setDecimals(2)
            widget.setRange(-1, 1_000_000)
            widget.setValue(-1)
            widget.setSpecialValueText("Nicht gesetzt")
        elif editor == "boolean":
            widget = QCheckBox("Ja")
            if self.action.currentText() == "UPDATE":
                # Partially checked is the explicit "unchanged" state.  This
                # prevents a fresh UPDATE editor from silently sending False.
                widget.setTristate(True)
                widget.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            widget = QLineEdit()
            widget.setPlaceholderText(spec.placeholder)
            if spec.clearable_on_update and self.action.currentText() == "UPDATE":
                widget.setPlaceholderText("Leer = unverändert; <CLEAR> = entfernen")
        widget.setEnabled(not spec.read_only)
        widget.setProperty("fieldKey", spec.key)
        if spec.clearable_on_update and self.action.currentText() == "UPDATE" and not spec.read_only:
            wrapper = ClearableField(widget)
            wrapper.setProperty("fieldKey", spec.key)
            return wrapper
        return widget

    def _set_widget_value(self, widget: QWidget, value: Any) -> None:
        if isinstance(widget, ClearableField):
            if value == "<CLEAR>":
                widget.set_clear(True)
                return
            widget = widget.editor
        if value in (None, ""):
            return
        if isinstance(widget, QLineEdit):
            widget.setText(str(value))
        elif isinstance(widget, QTextEdit):
            widget.setPlainText(str(value))
        elif isinstance(widget, QComboBox):
            index = widget.findData(str(value))
            widget.setCurrentIndex(index if index >= 0 else widget.findText(str(value)))
        elif isinstance(widget, TagEditor):
            widget.set_values(value if isinstance(value, (list, tuple, set)) else str(value).split(","))
        elif isinstance(widget, MultiSelectList):
            widget.setProperty("pendingValues", list(value) if isinstance(value, (list, tuple, set)) else [str(value)])
        elif isinstance(widget, (SearchField, AssigneeField)):
            widget.set_value(str(value))
        elif isinstance(widget, SearchMultiField):
            widget.set_values(value if isinstance(value, (list, tuple, set)) else [str(value)])
        elif isinstance(widget, QDateEdit):
            parsed = QDate.fromString(str(value), Qt.DateFormat.ISODate)
            if not parsed.isValid():
                parsed = QDate.fromString(str(value), "dd.MM.yy")
            if parsed.isValid():
                widget.setDate(parsed)
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value))
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))

    def _widget_value(self, widget: QWidget) -> Any:
        if isinstance(widget, ClearableField):
            if widget.is_clear():
                return "<CLEAR>"
            widget = widget.editor
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        if isinstance(widget, QTextEdit):
            return widget.toPlainText().strip()
        if isinstance(widget, QComboBox):
            return widget.currentData() if widget.currentData() is not None else widget.currentText()
        if isinstance(widget, TagEditor):
            return widget.values()
        if isinstance(widget, MultiSelectList):
            return widget.values()
        if isinstance(widget, SearchField):
            return widget.value()
        if isinstance(widget, AssigneeField):
            return widget.value()
        if isinstance(widget, SearchMultiField):
            return widget.values()
        if isinstance(widget, QDateEdit):
            return "" if widget.date() == widget.minimumDate() else widget.date().toString(Qt.DateFormat.ISODate)
        if isinstance(widget, QDoubleSpinBox):
            return "" if widget.value() == widget.minimum() else widget.value()
        if isinstance(widget, QCheckBox):
            if widget.isTristate() and widget.checkState() is Qt.CheckState.PartiallyChecked:
                return ""
            return widget.isChecked()
        return None

    def _load_options(self) -> None:
        issue_type = self.issue_type.currentText()
        project = self.project.text().strip()
        key = f"{self._options_prefix}:{issue_type}:{project}"
        self._tasks.submit(
            key,
            lambda token, _progress: {} if token.cancelled else self._facade.editor_options(issue_type, project),
            cancellable=True,
        )

    def _task_success(self, key: str, payload: object) -> None:
        expected = f"{self._options_prefix}:{self.issue_type.currentText()}:{self.project.text().strip()}"
        if key != expected or not isinstance(payload, Mapping):
            return
        options = payload
        for field_key, widget in self._field_widgets.items():
            spec = self._field_specs[field_key]
            editor_widget = widget.editor if isinstance(widget, ClearableField) else widget
            source = spec.option_source or field_key
            field_options = options.get(source) or options.get(field_key) or ()
            valid_options = [item for item in field_options if isinstance(item, SelectOption)]
            if isinstance(editor_widget, QComboBox):
                current = self._field_values.get(field_key, self._widget_value(widget))
                editor_widget.blockSignals(True)
                editor_widget.clear()
                editor_widget.addItem("Nicht gesetzt", "")
                for option in valid_options:
                    editor_widget.addItem(option.label, option.value)
                    index = editor_widget.count() - 1
                    model_item = getattr(editor_widget.model(), "item", lambda _index: None)(index)
                    if model_item is not None:
                        model_item.setEnabled(option.enabled)
                    editor_widget.setItemData(index, option.description, Qt.ItemDataRole.ToolTipRole)
                index = editor_widget.findData(current)
                editor_widget.setCurrentIndex(max(0, index))
                editor_widget.blockSignals(False)
            elif isinstance(editor_widget, MultiSelectList):
                selected = editor_widget.property("pendingValues") or self._field_values.get(field_key, [])
                editor_widget.set_options(valid_options, selected)
            elif isinstance(editor_widget, TagEditor):
                editor_widget.set_suggestions(option.label for option in valid_options)
        link_options = options.get("link_types", ())
        current_link_type = self.link_type.currentData()
        self.link_type.clear()
        for option in link_options:
            if isinstance(option, SelectOption):
                self.link_type.addItem(option.label, option.value)
        index = self.link_type.findData(current_link_type)
        if index >= 0:
            self.link_type.setCurrentIndex(index)

    def _task_failure(self, key: str, message: str, _detail: str) -> None:
        if key.startswith(self._options_prefix):
            self.update_hint.set_notice(f"Auswahldaten konnten nicht geladen werden: {message}", "warning")

    def _search_field(self, key: str) -> None:
        spec = self._field_specs.get(key)
        widget = self._field_widgets.get(key)
        if spec is None or widget is None:
            return
        editor_widget = widget.editor if isinstance(widget, ClearableField) else widget
        initial = ""
        if isinstance(editor_widget, SearchField):
            initial = editor_widget.input.text()
        elif isinstance(editor_widget, AssigneeField):
            initial = editor_widget.person.input.text()
        elif isinstance(editor_widget, SearchMultiField):
            initial = editor_widget.search_text.text()
        context = {
            "project": self.project.text().strip(),
            "issue_type": self.issue_type.currentText(),
            "field": key,
        }
        dialog = SearchDialog(
            spec.label,
            lambda query: self._facade.search(spec.option_source or key, query, context),
            self._tasks,
            initial_query=initial,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected is None:
            return
        selected = dialog.selected
        if isinstance(editor_widget, (SearchField, AssigneeField)):
            editor_widget.set_value(selected.value, selected.label)
        elif isinstance(editor_widget, SearchMultiField):
            editor_widget.add_value(selected.value, selected.label)

    def _search_link_issue(self) -> None:
        context = {"project": self.project.text().strip(), "issue_type": self.issue_type.currentText()}
        dialog = SearchDialog(
            "Ticket suchen",
            lambda query: self._facade.search("issues", query, context),
            self._tasks,
            initial_query=self.link_issue.input.text(),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected:
            self.link_issue.set_value(dialog.selected.value, dialog.selected.label)

    def _add_attachments(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(self, "Anhänge auswählen")
        known = {item.path for item in self._attachments}
        for value in paths:
            path = Path(value)
            if path not in known:
                try:
                    size = path.stat().st_size
                except OSError:
                    size = None
                self._attachments.append(AttachmentDraft(path, path.name, size))
        self._refresh_attachment_table()

    def _remove_attachments(self) -> None:
        rows = sorted({index.row() for index in self.attachments_table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._attachments):
                self._attachments.pop(row)
        self._refresh_attachment_table()

    def _refresh_attachment_table(self) -> None:
        self.attachments_table.setRowCount(len(self._attachments))
        for row, attachment in enumerate(self._attachments):
            size = "—" if attachment.size_bytes is None else _format_size(attachment.size_bytes)
            for column, value in enumerate((attachment.display_name or attachment.path.name, size, str(attachment.path))):
                self.attachments_table.setItem(row, column, QTableWidgetItem(value))

    def _add_link(self) -> None:
        issue_key = self.link_issue.value()
        link_type = self.link_type.currentData() or self.link_type.currentText()
        if not issue_key or not link_type:
            QMessageBox.information(self, "Link unvollständig", "Bitte Link-Typ und Ziel-Ticket auswählen.")
            return
        self._links.append(
            LinkDraft(
                str(link_type),
                str(issue_key),
                self.link_issue.input.text(),
                str(self.link_direction.currentData()),
            )
        )
        self.link_issue.clear_selection()
        self._refresh_link_table()

    def _remove_links(self) -> None:
        rows = sorted({index.row() for index in self.links_table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._links):
                self._links.pop(row)
        self._refresh_link_table()

    def _refresh_link_table(self) -> None:
        self.links_table.setRowCount(len(self._links))
        for row, link in enumerate(self._links):
            values = (link.link_type, "ausgehend" if link.direction == "outward" else "eingehend", link.issue_label or link.issue_key)
            for column, value in enumerate(values):
                self.links_table.setItem(row, column, QTableWidgetItem(value))

    def draft(self) -> TicketDraft:
        self._capture_field_values()
        values = dict(self._field_values)
        summary = str(values.pop("summary", ""))
        return TicketDraft(
            local_id=self._source.local_id,
            action=self.action.currentText(),
            project=self.project.text().strip(),
            issue_type=self.issue_type.currentText(),
            summary=summary,
            values=values,
            attachments=list(self._attachments),
            links=list(self._links),
            jira_key=(self._source.jira_key if self._has_persisted_issue else self.jira_key.text()).strip(),
            jira_updated=self._source.jira_updated,
        )

    def _save(self) -> None:
        self.completion = "save"
        self.accept()

    def _preview(self) -> None:
        self.completion = "preview"
        self.accept()

    def _set_focus_order(self) -> None:
        controls: list[QWidget] = [self.action, self.project, self.issue_type, self.jira_key]
        controls.extend(
            widget.editor if isinstance(widget, ClearableField) else widget for widget in self._field_widgets.values()
        )
        controls.extend([self.tabs, self.save_button, self.preview_button])
        for first, second in zip(controls, controls[1:], strict=False):
            QWidget.setTabOrder(first, second)


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
