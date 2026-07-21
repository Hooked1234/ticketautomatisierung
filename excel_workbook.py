"""Safe, transport-independent helpers for Excel workbook evolution."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from ticket_schema import ALL_TICKET_COLUMNS, DEFAULT_VISIBLE_COLUMNS, WORKBOOK_SHEETS


JIRA_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
EXCEL_DATE_FORMAT = "dd.mm.yy"
WORKSHEET_HEADERS: Mapping[str, Tuple[str, ...]] = {
    "Attachments": ("RowId", "JiraKey", "FilePath", "Result", "ErrorMessage"),
    "Ticket_Links": (
        "RowId",
        "JiraKey",
        "LinkType",
        "TargetIssue",
        "Result",
        "ErrorMessage",
    ),
    "Einstellungen": ("Category", "Setting", "Value", "Description"),
    "Metadaten": (
        "Components",
        "IssueTypes",
        "Priorities",
        "Actions",
        "Projects",
        "LegacyStatus",
    ),
    "Sprints": ("BoardId", "SprintId", "SprintName", "State", "RefreshedAt"),
    "Kommentare": (
        "JiraKey",
        "Author",
        "Created",
        "Updated",
        "Comment",
        "IsNew",
    ),
    "Sync_Log": (
        "Timestamp",
        "RowId",
        "Action",
        "JiraKey",
        "Result",
        "Message",
    ),
    "Konflikte": (
        "JiraKey",
        "LoadedUpdated",
        "CurrentUpdated",
        "Message",
    ),
    "Dashboard": ("Metric", "Value"),
}


class WorkbookSafetyError(RuntimeError):
    """Raised when a workbook operation would be destructive or ambiguous."""


@dataclass(frozen=True)
class WorkbookMigrationPlan:
    missing_columns: Tuple[str, ...]
    missing_sheets: Tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.missing_columns or self.missing_sheets)


def workbook_mode(path: Path | str, migrate_existing: bool) -> str:
    """Return create/migrate while refusing an implicit overwrite."""
    target = Path(path)
    if target.suffix.lower() != ".xlsm":
        raise WorkbookSafetyError("Ziel muss eine XLSM-Datei sein.")
    if target.exists():
        if not migrate_existing:
            raise WorkbookSafetyError(
                f"Die vorhandene Arbeitsmappe '{target}' wird nicht ueberschrieben. "
                "Fuer eine gesicherte Erweiterung --migrate-existing verwenden."
            )
        if not target.is_file():
            raise WorkbookSafetyError(f"Der Zielpfad ist keine Datei: {target}")
        if target.suffix.lower() != ".xlsm":
            raise WorkbookSafetyError(
                "Die sichere Migration ist ausschliesslich fuer XLSM-Dateien vorgesehen."
            )
        return "migrate"
    if migrate_existing:
        raise WorkbookSafetyError(
            f"Migration angefordert, aber die Arbeitsmappe existiert nicht: {target}"
        )
    return "create"


def create_workbook_backup(
    source: Path | str,
    backup_directory: Optional[Path | str] = None,
    timestamp: Optional[datetime] = None,
) -> Path:
    """Copy an XLSM before migration and return the collision-safe backup path."""
    source_path = Path(source)
    if not source_path.is_file():
        raise WorkbookSafetyError(f"Arbeitsmappe nicht gefunden: {source_path}")
    if source_path.suffix.lower() != ".xlsm":
        raise WorkbookSafetyError("Nur XLSM-Dateien werden als XLSM migriert.")

    destination_directory = (
        Path(backup_directory) if backup_directory is not None else source_path.parent
    )
    destination_directory.mkdir(parents=True, exist_ok=True)
    stamp = (timestamp or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base_name = f"{source_path.stem}.backup-{stamp}"
    destination = destination_directory / f"{base_name}{source_path.suffix}"
    counter = 1
    while destination.exists():
        destination = destination_directory / (
            f"{base_name}-{counter}{source_path.suffix}"
        )
        counter += 1

    try:
        shutil.copy2(source_path, destination)
    except OSError as error:
        raise WorkbookSafetyError(
            f"Sicherung konnte nicht erstellt werden: {destination}"
        ) from error
    if source_path.read_bytes() != destination.read_bytes():
        destination.unlink(missing_ok=True)
        raise WorkbookSafetyError("Sicherung ist nicht bytegleich; Migration abgebrochen.")
    return destination


def normalize_headers(values: Iterable[Any]) -> Tuple[str, ...]:
    return tuple(str(value).strip() for value in values if str(value or "").strip())


def missing_ticket_columns(
    existing_headers: Sequence[Any], desired_headers: Sequence[str] = ALL_TICKET_COLUMNS
) -> Tuple[str, ...]:
    existing = set(normalize_headers(existing_headers))
    return tuple(header for header in desired_headers if header not in existing)


def plan_workbook_migration(
    existing_headers: Sequence[Any], existing_sheets: Sequence[Any]
) -> WorkbookMigrationPlan:
    sheet_names = set(normalize_headers(existing_sheets))
    return WorkbookMigrationPlan(
        missing_columns=missing_ticket_columns(existing_headers),
        missing_sheets=tuple(name for name in WORKBOOK_SHEETS if name not in sheet_names),
    )


def jira_browse_url(jira_url: str, jira_key: Any) -> str:
    base_url = str(jira_url or "").strip().rstrip("/")
    key = str(jira_key or "").strip().upper()
    if not base_url:
        raise ValueError("JIRA_URL fehlt fuer den Jira-Key-Hyperlink.")
    if not JIRA_KEY_PATTERN.fullmatch(key):
        raise ValueError(f"Ungueltiger Jira Key fuer Hyperlink: '{jira_key or ''}'.")
    return f"{base_url}/browse/{key}"


def set_jira_key_hyperlink(cell: Any, jira_url: str, jira_key: Any) -> None:
    """Store only the key as display text and attach the Jira browse URL."""
    key = str(jira_key or "").strip().upper()
    if not key:
        cell.value = ""
        cell.hyperlink = None
        return
    cell.value = key
    cell.hyperlink = jira_browse_url(jira_url, key)
    cell.style = "Hyperlink"


def apply_jira_key_hyperlinks(
    sheet: Any, headers: Mapping[str, int], jira_url: str
) -> int:
    jira_key_column = headers.get("JiraKey")
    if jira_key_column is None:
        raise ValueError("Excel-Spalte JiraKey fehlt.")
    applied = 0
    for row_index in range(2, sheet.max_row + 1):
        cell = sheet.cell(row=row_index, column=jira_key_column)
        if not str(cell.value or "").strip():
            continue
        set_jira_key_hyperlink(cell, jira_url, cell.value)
        applied += 1
    return applied


def column_visibility_rows(
    visible_columns: Sequence[str],
    all_columns: Sequence[str] = ALL_TICKET_COLUMNS,
) -> Tuple[Tuple[str, str, str, str], ...]:
    visible = set(visible_columns or DEFAULT_VISIBLE_COLUMNS)
    return tuple(
        (
            "Columns",
            column,
            "yes" if column in visible else "no",
            "Ausblenden loescht keine Zellinhalte.",
        )
        for column in all_columns
    )
