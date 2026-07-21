"""Create a new XLSM or explicitly migrate an existing one without VBA loss."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import requests

try:
    import win32com.client as win32
    from pywintypes import com_error
except ImportError:  # Offline unit tests do not require Excel COM.
    win32 = None
    com_error = RuntimeError

from create_tickets import (
    jira_headers,
    load_config,
    test_jira_connection,
    test_project_access,
)
from excel_workbook import (
    EXCEL_DATE_FORMAT,
    WORKSHEET_HEADERS,
    column_visibility_rows,
    create_workbook_backup,
    jira_browse_url,
    missing_ticket_columns,
    workbook_mode,
)
from ticket_schema import (
    ALL_TICKET_COLUMNS,
    DEFAULT_VISIBLE_COLUMNS,
    SUPPORTED_ACTIONS,
    SUPPORTED_ISSUE_TYPES,
)


COLUMNS = list(ALL_TICKET_COLUMNS)
EXAMPLE_VALUES = {
    "Action": "CREATE",
    "Project": "DAH",
    "IssueType": "Bug",
    "Summary": "Beispiel: Login-Bug auf iOS",
    "Description": "Nutzer kann sich nach Update nicht mehr einloggen.",
    "Priority": "High",
    "Components": "Frontend",
    "Labels": "frontend,urgent",
    "DueDate": datetime(2026, 6, 15),
    "Status": "NEU",
    "ExternalId": "EXCEL-001",
}
STATUS_OPTIONS = ["NEU", "EDIT"]
OFFLINE_COMPONENTS = ["Backend", "Frontend"]
OFFLINE_ISSUE_TYPES = list(SUPPORTED_ISSUE_TYPES)
OFFLINE_PRIORITIES = ["Medium", "High", "Low"]
OUTPUT_FILE = "tickets.xlsm"
MAX_ROWS = 1000

XL_VALIDATE_LIST = 3
XL_VALID_ALERT_STOP = 1
XL_BETWEEN = 1
XL_SHEET_HIDDEN = 0
XL_OPENXML_MACRO = 52
VBEXT_CT_STD_MODULE = 1


def _require_success(response: requests.Response, operation: str) -> None:
    if response.status_code != 200:
        raise RuntimeError(f"{operation} fehlgeschlagen: HTTP {response.status_code}")


def fetch_components(config: Mapping[str, Any]) -> List[str]:
    url = f"{config['JIRA_URL']}/rest/api/2/project/{config['PROJECT_KEY']}/components"
    response = requests.get(url, headers=jira_headers(config), timeout=30)
    _require_success(response, "Components laden")
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Components laden: unerwartetes Antwortformat")
    return [
        str(component["name"])
        for component in payload
        if isinstance(component, Mapping) and component.get("name")
    ]


def fetch_issue_types(config: Mapping[str, Any]) -> List[str]:
    url = f"{config['JIRA_URL']}/rest/api/2/project/{config['PROJECT_KEY']}"
    response = requests.get(url, headers=jira_headers(config), timeout=30)
    _require_success(response, "Issue Types laden")
    payload = response.json()
    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("issueTypes"), list
    ):
        raise RuntimeError("Issue Types laden: unerwartetes Antwortformat")
    available = {
        str(issue_type["name"]).casefold()
        for issue_type in payload["issueTypes"]
        if isinstance(issue_type, Mapping)
        and issue_type.get("name")
        and not issue_type.get("subtask")
    }
    return [
        issue_type
        for issue_type in SUPPORTED_ISSUE_TYPES
        if issue_type.casefold() in available
    ]


def fetch_priorities(config: Mapping[str, Any]) -> List[str]:
    url = f"{config['JIRA_URL']}/rest/api/2/priority"
    response = requests.get(url, headers=jira_headers(config), timeout=30)
    _require_success(response, "Priorities laden")
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Priorities laden: unerwartetes Antwortformat")
    return [
        str(priority["name"])
        for priority in payload
        if isinstance(priority, Mapping) and priority.get("name")
    ]


def col_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def write_list(sheet: Any, col_idx: int, header: str, values: Sequence[str]) -> str:
    letter = col_letter(col_idx)
    sheet.Columns(col_idx).ClearContents()
    sheet.Cells(1, col_idx).Value = header
    for row_idx, value in enumerate(values, start=2):
        sheet.Cells(row_idx, col_idx).Value = value
    last_row = max(2, len(values) + 1)
    return f"='{sheet.Name}'!${letter}$2:${letter}${last_row}"


def add_validation(sheet: Any, col_idx: int, formula: str) -> None:
    letter = col_letter(col_idx)
    cell_range = sheet.Range(f"{letter}2:{letter}{MAX_ROWS}")
    cell_range.Validation.Delete()
    cell_range.Validation.Add(
        XL_VALIDATE_LIST, XL_VALID_ALERT_STOP, XL_BETWEEN, formula
    )
    cell_range.Validation.InCellDropdown = True
    cell_range.Validation.IgnoreBlank = True


def build_macro(comp_col_idx: int) -> str:
    return f"""Private Sub Worksheet_Change(ByVal Target As Range)
    Const COMP_COL As Long = {comp_col_idx}
    Dim newVal As String, oldVal As String
    If Target.Cells.Count > 1 Then Exit Sub
    If Target.Column <> COMP_COL Then Exit Sub
    If Target.Row = 1 Then Exit Sub
    On Error GoTo CleanUp
    Application.EnableEvents = False
    newVal = CStr(Target.Value)
    Application.Undo
    oldVal = CStr(Target.Value)
    Target.Value = newVal
    If newVal <> "" And oldVal <> "" Then
        If InStr(1, ", " & oldVal & ", ", ", " & newVal & ", ", vbTextCompare) = 0 Then
            Target.Value = oldVal & ", " & newVal
        Else
            Target.Value = oldVal
        End If
    End If
CleanUp:
    Application.EnableEvents = True
End Sub
"""


def build_button_module() -> str:
    return '''Sub Tickets_Erstellen()
    Dim base As String, pyExe As String, batPath As String, wbName As String
    Dim wsh As Object, fso As Object, ts As Object

    base = ThisWorkbook.Path
    If Len(base) = 0 Then
        MsgBox "Bitte die Arbeitsmappe zuerst speichern.", vbExclamation, "Jira Tickets"
        Exit Sub
    End If
    wbName = ThisWorkbook.Name

    If Dir(base & "\\create_tickets.py") = "" Then
        MsgBox "create_tickets.py wurde nicht gefunden in:" & vbCrLf & base, vbCritical, "Jira Tickets"
        Exit Sub
    End If

    pyExe = FindPython(base)
    If Len(pyExe) = 0 Then
        MsgBox "Kein Python gefunden." & vbCrLf & _
               "Erwartet: .venv\\Scripts\\python.exe im Ordner der Datei," & vbCrLf & _
               "oder 'py' / 'python' im System-PATH.", vbCritical, "Jira Tickets"
        Exit Sub
    End If

    If MsgBox("Die Datei wird gespeichert, kurz geschlossen und die Ticket-" & _
              "Erstellung gestartet." & vbCrLf & _
              "Danach wird sie automatisch wieder geoeffnet." & vbCrLf & vbCrLf & _
              "Fortfahren?", vbQuestion + vbYesNo, "Jira Tickets") <> vbYes Then
        Exit Sub
    End If

    Set fso = CreateObject("Scripting.FileSystemObject")
    batPath = fso.BuildPath(Environ$("TEMP"), "jira_tickets_run.bat")
    Set ts = fso.CreateTextFile(batPath, True)
    ts.WriteLine "@echo off"
    ts.WriteLine "cd /d """ & base & """"
    ts.WriteLine "echo Warte, bis Excel die Datei freigibt..."
    ts.WriteLine ":waitloop"
    ts.WriteLine "ren """ & wbName & """ """ & wbName & """ 2>nul && goto ready"
    ts.WriteLine "timeout /t 1 >nul"
    ts.WriteLine "goto waitloop"
    ts.WriteLine ":ready"
    ts.WriteLine "echo Starte Ticket-Erstellung..."
    ts.WriteLine """" & pyExe & """ ""create_tickets.py"""
    ts.WriteLine "echo."
    ts.WriteLine "echo Fertig. Excel wird wieder geoeffnet..."
    ts.WriteLine "timeout /t 4 >nul"
    ts.WriteLine "start """" """ & wbName & """"
    ts.WriteLine "del ""%~f0"""
    ts.Close

    Set wsh = CreateObject("WScript.Shell")
    wsh.Run """" & batPath & """", 1, False

    ThisWorkbook.Save
    ThisWorkbook.Close SaveChanges:=False
End Sub

Sub Jira_Daten_Aktualisieren()
    MsgBox "Die vollstaendige Metadatenaktualisierung folgt in Phase 2.", _
           vbInformation, "Jira-Daten aktualisieren"
End Sub

Private Function FindPython(base As String) As String
    Dim c As String
    c = base & "\\.venv\\Scripts\\python.exe"
    If Dir(c) <> "" Then FindPython = c: Exit Function
    c = base & "\\venv\\Scripts\\python.exe"
    If Dir(c) <> "" Then FindPython = c: Exit Function
    If ExistsOnPath("py.exe") Then FindPython = "py": Exit Function
    If ExistsOnPath("python.exe") Then FindPython = "python": Exit Function
    FindPython = ""
End Function

Private Function ExistsOnPath(exeName As String) As Boolean
    Dim wsh As Object, ex As Object
    On Error GoTo fail
    Set wsh = CreateObject("WScript.Shell")
    Set ex = wsh.Exec("cmd /c where " & exeName)
    Do While ex.Status = 0
        DoEvents
    Loop
    ExistsOnPath = (ex.ExitCode = 0)
    Exit Function
fail:
    ExistsOnPath = False
End Function
'''


def _worksheet(workbook: Any, name: str) -> Optional[Any]:
    for index in range(1, workbook.Worksheets.Count + 1):
        sheet = workbook.Worksheets(index)
        if str(sheet.Name) == name:
            return sheet
    return None


def _ensure_worksheet(workbook: Any, name: str) -> Tuple[Any, bool]:
    existing = _worksheet(workbook, name)
    if existing is not None:
        return existing, False
    sheet = workbook.Worksheets.Add(After=workbook.Worksheets(workbook.Worksheets.Count))
    sheet.Name = name
    return sheet, True


def _header_map(sheet: Any) -> Dict[str, int]:
    max_column = max(1, int(sheet.UsedRange.Columns.Count))
    headers: Dict[str, int] = {}
    for column in range(1, max_column + 1):
        value = sheet.Cells(1, column).Value
        if value is not None and str(value).strip():
            headers[str(value).strip()] = column
    return headers


def _append_missing_headers(sheet: Any, headers: Sequence[str]) -> Tuple[str, ...]:
    existing = _header_map(sheet)
    missing = missing_ticket_columns(tuple(existing), headers)
    next_column = max(existing.values(), default=0) + 1
    for header in missing:
        sheet.Cells(1, next_column).Value = header
        existing[header] = next_column
        next_column += 1
    return missing


def _prepare_support_sheets(workbook: Any) -> Dict[str, bool]:
    created: Dict[str, bool] = {}
    for name, headers in WORKSHEET_HEADERS.items():
        sheet, was_created = _ensure_worksheet(workbook, name)
        _append_missing_headers(sheet, headers)
        created[name] = was_created
    return created


def _write_settings(sheet: Any, visible_columns: Sequence[str]) -> None:
    for row_index, row in enumerate(column_visibility_rows(visible_columns), start=2):
        for column_index, value in enumerate(row, start=1):
            sheet.Cells(row_index, column_index).Value = value


def _write_metadata(
    sheet: Any,
    components: Sequence[str],
    issue_types: Sequence[str],
    priorities: Sequence[str],
    projects: Sequence[str],
) -> Mapping[str, str]:
    header_map = _header_map(sheet)
    values = {
        "Components": components,
        "IssueTypes": issue_types,
        "Priorities": priorities,
        "Actions": SUPPORTED_ACTIONS,
        "Projects": projects,
        "LegacyStatus": STATUS_OPTIONS,
    }
    formulas: Dict[str, str] = {}
    for header, options in values.items():
        formulas[header] = write_list(sheet, header_map[header], header, options)
    return formulas


def _populate_default_project(sheet: Any, project_key: str) -> int:
    headers = _header_map(sheet)
    project_column = headers.get("Project")
    summary_column = headers.get("Summary")
    if project_column is None or summary_column is None:
        return 0
    last_row = int(sheet.Cells(sheet.Rows.Count, summary_column).End(-4162).Row)
    populated = 0
    for row_index in range(2, max(2, last_row) + 1):
        summary = str(sheet.Cells(row_index, summary_column).Value or "").strip()
        project = str(sheet.Cells(row_index, project_column).Value or "").strip()
        if summary and not project:
            sheet.Cells(row_index, project_column).Value = project_key
            populated += 1
    return populated


def _apply_ticket_formatting(
    sheet: Any,
    formulas: Mapping[str, str],
    visible_columns: Sequence[str],
    only_visibility_for: Optional[Sequence[str]] = None,
) -> None:
    headers = _header_map(sheet)
    validation_sources = {
        "Action": "Actions",
        "Project": "Projects",
        "IssueType": "IssueTypes",
        "Priority": "Priorities",
        "Components": "Components",
        "Status": "LegacyStatus",
    }
    for column_name, source_name in validation_sources.items():
        if column_name in headers:
            add_validation(sheet, headers[column_name], formulas[source_name])

    for column_name in ("DueDate", "StartDate", "EndDate"):
        if column_name in headers:
            letter = col_letter(headers[column_name])
            sheet.Range(f"{letter}2:{letter}{MAX_ROWS}").NumberFormat = EXCEL_DATE_FORMAT

    visibility_targets = set(only_visibility_for or headers)
    visible = set(visible_columns)
    for column_name in visibility_targets:
        column_index = headers.get(column_name)
        if column_index is not None:
            sheet.Columns(column_index).Hidden = column_name not in visible


def _apply_existing_jira_hyperlinks(sheet: Any, jira_url: str) -> int:
    headers = _header_map(sheet)
    column = headers.get("JiraKey")
    if column is None:
        return 0
    applied = 0
    last_row = max(1, int(sheet.UsedRange.Rows.Count))
    for row_index in range(2, last_row + 1):
        cell = sheet.Cells(row_index, column)
        key = str(cell.Value or "").strip().upper()
        if not key:
            continue
        url = jira_browse_url(jira_url, key)
        if cell.Hyperlinks.Count:
            cell.Hyperlinks.Delete()
        sheet.Hyperlinks.Add(Anchor=cell, Address=url, TextToDisplay=key)
        applied += 1
    return applied


def _add_button(
    sheet: Any,
    name: str,
    caption: str,
    macro: str,
    start_column: int,
    start_row: int,
) -> None:
    left = col_letter(start_column)
    right = col_letter(start_column + 2)
    target = sheet.Range(f"{left}{start_row}:{right}{start_row + 1}")
    button = sheet.Buttons().Add(target.Left, target.Top, target.Width, target.Height)
    button.Name = name
    button.Caption = caption
    button.OnAction = macro


def _visible_columns(config: Mapping[str, Any]) -> Tuple[str, ...]:
    app_config = config.get("APP_CONFIG")
    if app_config is not None:
        return tuple(app_config.excel.default_visible_columns)
    return DEFAULT_VISIBLE_COLUMNS


def _allowed_projects(config: Mapping[str, Any]) -> Tuple[str, ...]:
    projects = config.get("ALLOWED_PROJECTS")
    if projects:
        return tuple(str(project) for project in projects)
    return (str(config["PROJECT_KEY"]),)


def _create_new_workbook(
    excel: Any,
    output_path: Path,
    config: Mapping[str, Any],
    components: Sequence[str],
    issue_types: Sequence[str],
    priorities: Sequence[str],
) -> None:
    workbook = excel.Workbooks.Add()
    try:
        tickets = workbook.Worksheets(1)
        tickets.Name = "Tickets"
        for column_index, name in enumerate(COLUMNS, start=1):
            tickets.Cells(1, column_index).Value = name
            value = EXAMPLE_VALUES.get(name)
            if value is not None:
                tickets.Cells(2, column_index).Value = value

        _prepare_support_sheets(workbook)
        metadata = _worksheet(workbook, "Metadaten")
        settings = _worksheet(workbook, "Einstellungen")
        if metadata is None or settings is None:
            raise RuntimeError("Arbeitsmappenstruktur konnte nicht erstellt werden.")
        formulas = _write_metadata(
            metadata,
            components,
            issue_types,
            priorities,
            _allowed_projects(config),
        )
        visible_columns = _visible_columns(config)
        _write_settings(settings, visible_columns)
        _apply_ticket_formatting(tickets, formulas, visible_columns)
        metadata.Visible = XL_SHEET_HIDDEN

        try:
            component_column = COLUMNS.index("Components") + 1
            module = workbook.VBProject.VBComponents(tickets.CodeName).CodeModule
            module.AddFromString(build_macro(component_column))
            standard_module = workbook.VBProject.VBComponents.Add(VBEXT_CT_STD_MODULE)
            standard_module.Name = "TicketMakros"
            standard_module.CodeModule.AddFromString(build_button_module())
        except com_error as error:
            raise RuntimeError(
                "VBA-Makro konnte nicht eingefuegt werden. In Excel muss der "
                "Zugriff auf das VBA-Projektobjektmodell aktiviert sein."
            ) from error

        button_column = len(COLUMNS) + 2
        _add_button(
            tickets,
            "btnTicketsErstellen",
            "Tickets in Jira erstellen",
            "Tickets_Erstellen",
            button_column,
            1,
        )
        _add_button(
            tickets,
            "btnJiraDatenAktualisieren",
            "Jira-Daten aktualisieren",
            "Jira_Daten_Aktualisieren",
            button_column,
            4,
        )
        workbook.SaveAs(str(output_path), FileFormat=XL_OPENXML_MACRO)
    finally:
        workbook.Close(SaveChanges=False)


def _migrate_existing_workbook(
    excel: Any,
    output_path: Path,
    config: Mapping[str, Any],
    components: Sequence[str],
    issue_types: Sequence[str],
    priorities: Sequence[str],
) -> Tuple[Tuple[str, ...], Tuple[str, ...], int]:
    backup_path = create_workbook_backup(output_path)
    print(f"Sicherung erstellt: {backup_path}")
    workbook = excel.Workbooks.Open(str(output_path))
    try:
        tickets = _worksheet(workbook, "Tickets")
        if tickets is None:
            raise RuntimeError("Migration abgebrochen: Blatt 'Tickets' fehlt.")
        existing_sheet_names = tuple(
            str(workbook.Worksheets(index).Name)
            for index in range(1, workbook.Worksheets.Count + 1)
        )
        existing_headers = tuple(_header_map(tickets))
        missing_columns = missing_ticket_columns(existing_headers)
        missing_sheets = tuple(
            name for name in WORKSHEET_HEADERS if name not in existing_sheet_names
        )
        _append_missing_headers(tickets, ALL_TICKET_COLUMNS)
        if "Project" in missing_columns:
            _populate_default_project(tickets, str(config["PROJECT_KEY"]))
        created = _prepare_support_sheets(workbook)
        metadata = _worksheet(workbook, "Metadaten")
        settings = _worksheet(workbook, "Einstellungen")
        if metadata is None or settings is None:
            raise RuntimeError("Arbeitsmappenstruktur konnte nicht migriert werden.")
        formulas = _write_metadata(
            metadata,
            components,
            issue_types,
            priorities,
            _allowed_projects(config),
        )
        visible_columns = _visible_columns(config)
        if created.get("Einstellungen"):
            _write_settings(settings, visible_columns)
        _apply_ticket_formatting(
            tickets,
            formulas,
            visible_columns,
            only_visibility_for=missing_columns,
        )
        metadata.Visible = XL_SHEET_HIDDEN
        hyperlink_count = _apply_existing_jira_hyperlinks(
            tickets, str(config["JIRA_URL"])
        )
        workbook.Save()
        return missing_columns, missing_sheets, hyperlink_count
    finally:
        workbook.Close(SaveChanges=False)


def _load_metadata(config: Mapping[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    try:
        print("Teste Jira-Verbindung...")
        test_jira_connection(config)
        print("Teste Projektzugriff...")
        test_project_access(config)
        print("Lade Auswahlwerte aus Jira...")
        components = fetch_components(config)
        issue_types = fetch_issue_types(config)
        priorities = fetch_priorities(config)
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print("[WARN] Jira nicht erreichbar - nutze lokale Standardlisten.")
        print(f"[WARN] Ursache: {error}")
        components = OFFLINE_COMPONENTS
        issue_types = OFFLINE_ISSUE_TYPES
        priorities = OFFLINE_PRIORITIES
    print(f"  Components:  {len(components)}")
    print(f"  IssueTypes:  {len(issue_types)}")
    print(f"  Priorities:  {len(priorities)}")
    return components, issue_types, priorities


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=OUTPUT_FILE, help="Ziel-XLSM")
    parser.add_argument(
        "--migrate-existing",
        action="store_true",
        help="Vorhandene XLSM nach automatischer Sicherung additiv erweitern.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    output_path = Path(args.output).resolve()
    mode = workbook_mode(output_path, args.migrate_existing)
    config = load_config()
    components, issue_types, priorities = _load_metadata(config)

    if win32 is None:
        raise RuntimeError("pywin32 fehlt; Excel-Automatisierung ist nicht verfuegbar.")

    print("Starte Excel...")
    excel = win32.gencache.EnsureDispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        if mode == "create":
            _create_new_workbook(
                excel, output_path, config, components, issue_types, priorities
            )
            print(f"\nFertig: {output_path}")
        else:
            columns, sheets, hyperlinks = _migrate_existing_workbook(
                excel, output_path, config, components, issue_types, priorities
            )
            print(f"\nMigriert: {output_path}")
            print(f"  Neue Spalten: {len(columns)}")
            print(f"  Neue Blaetter: {len(sheets)}")
            print(f"  Jira-Hyperlinks: {hyperlinks}")
    finally:
        excel.Quit()

    print("Der bestehende VBA-Einstiegspunkt Tickets_Erstellen bleibt erhalten.")
    print("Echtlauf oder Dry Run steuert weiterhin DRY_RUN in der .env.")


if __name__ == "__main__":
    try:
        main()
    except (
        KeyError,
        OSError,
        RuntimeError,
        ValueError,
        requests.RequestException,
        com_error,
    ) as error:
        print(f"\nAbbruch: {error}", file=sys.stderr)
        sys.exit(1)
