"""
Erzeugt eine tickets.xlsm mit Jira-gestuetzten Dropdowns und einem VBA-Makro
fuer Mehrfachauswahl in der Components-Spalte (Verhalten wie in Jira:
Wert aus der Liste anklicken -> wird an die Zelle angehaengt; nur Jira-Werte
sind erlaubt).

Voraussetzungen (auf diesem Windows-Rechner):
  - Microsoft Excel installiert
  - pywin32 installiert (pip install pywin32)
  - Excel-Einstellung aktiviert:
      Datei > Optionen > Trust Center > Einstellungen fuer das Trust Center
      > Makroeinstellungen > "Zugriff auf das VBA-Projektobjektmodell vertrauen"
    (sonst kann das Makro nicht eingefuegt werden)

Aufruf:
    python build_excel_macro.py

Die Dropdown-Werte werden live aus Jira gelesen (Components, IssueTypes,
Priorities) anhand der Zugangsdaten aus der .env.
"""

import os
import sys
from typing import Dict, List

import requests
import win32com.client as win32

from create_tickets import (
    load_config,
    jira_headers,
    test_jira_connection,
    test_project_access,
)


COLUMNS = [
    "Summary",
    "Description",
    "IssueType",
    "Priority",
    "Assignee",
    "Labels",
    "Components",
    "DueDate",
    "Status",
    "JiraKey",
    "ErrorMessage",
    "CreatedAt",
    "ExternalId",
]

EXAMPLE_ROW = [
    "Beispiel: Login-Bug auf iOS",
    "Nutzer kann sich nach Update nicht mehr einloggen.",
    "Bug",
    "High",
    "",
    "frontend,urgent",
    "",
    "2026-06-15",
    "NEU",
    "",
    "",
    "",
    "EXCEL-001",
]

STATUS_OPTIONS = ["NEU", "EDIT"]

OFFLINE_COMPONENTS = ["Backend", "Frontend"]
OFFLINE_ISSUE_TYPES = ["Task", "Bug", "Story"]
OFFLINE_PRIORITIES = ["Medium", "High", "Low"]

OUTPUT_FILE = "tickets.xlsm"
MAX_ROWS = 1000

# Excel-Konstanten
XL_VALIDATE_LIST = 3
XL_VALID_ALERT_STOP = 1
XL_BETWEEN = 1
XL_SHEET_HIDDEN = 0
XL_OPENXML_MACRO = 52
VBEXT_CT_STD_MODULE = 1


def fetch_components(config: Dict[str, str]) -> List[str]:
    url = f"{config['JIRA_URL']}/rest/api/2/project/{config['PROJECT_KEY']}/components"
    response = requests.get(url, headers=jira_headers(config), timeout=30)
    if response.status_code != 200:
        raise RuntimeError(
            f"Components laden fehlgeschlagen: {response.status_code} {response.text}"
        )
    return [c["name"] for c in response.json() if c.get("name")]


def fetch_issue_types(config: Dict[str, str]) -> List[str]:
    url = f"{config['JIRA_URL']}/rest/api/2/project/{config['PROJECT_KEY']}"
    response = requests.get(url, headers=jira_headers(config), timeout=30)
    if response.status_code != 200:
        raise RuntimeError(
            f"IssueTypes laden fehlgeschlagen: {response.status_code} {response.text}"
        )
    types = response.json().get("issueTypes", [])
    names = [t["name"] for t in types if t.get("name") and not t.get("subtask")]
    return names or [t["name"] for t in types if t.get("name")]


def fetch_priorities(config: Dict[str, str]) -> List[str]:
    url = f"{config['JIRA_URL']}/rest/api/2/priority"
    response = requests.get(url, headers=jira_headers(config), timeout=30)
    if response.status_code != 200:
        raise RuntimeError(
            f"Priorities laden fehlgeschlagen: {response.status_code} {response.text}"
        )
    return [p["name"] for p in response.json() if p.get("name")]


def col_letter(idx: int) -> str:
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def write_list(listen, col_idx: int, header: str, values: List[str]) -> str:
    letter = col_letter(col_idx)
    listen.Cells(1, col_idx).Value = header
    for i, value in enumerate(values, start=2):
        listen.Cells(i, col_idx).Value = value
    last_row = len(values) + 1
    return f"=Listen!${letter}$2:${letter}${last_row}"


def add_validation(sheet, col_idx: int, formula: str) -> None:
    letter = col_letter(col_idx)
    rng = sheet.Range(f"{letter}2:{letter}{MAX_ROWS}")
    rng.Validation.Delete()
    rng.Validation.Add(XL_VALIDATE_LIST, XL_VALID_ALERT_STOP, XL_BETWEEN, formula)
    rng.Validation.InCellDropdown = True
    rng.Validation.IgnoreBlank = True


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
    # Triple-Single-Quotes, damit die vielen doppelten Anfuehrungszeichen im
    # VBA-Code nicht mit dem Python-String kollidieren.
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


def main() -> None:
    config = load_config()

    try:
        print("Teste Jira-Verbindung...")
        test_jira_connection(config)
        print("Teste Projektzugriff...")
        test_project_access(config)

        print("Lade Auswahlwerte aus Jira...")
        components = fetch_components(config)
        issue_types = fetch_issue_types(config)
        priorities = fetch_priorities(config)
        print(f"  Components:  {len(components)}")
        print(f"  IssueTypes:  {len(issue_types)}")
        print(f"  Priorities:  {len(priorities)}")
    except Exception as fetch_error:
        print("[WARN] Jira nicht erreichbar - nutze lokale Standardlisten.")
        print(f"[WARN] Ursache: {fetch_error}")
        components = OFFLINE_COMPONENTS
        issue_types = OFFLINE_ISSUE_TYPES
        priorities = OFFLINE_PRIORITIES
        print(f"  Components:  {len(components)} (offline)")
        print(f"  IssueTypes:  {len(issue_types)} (offline)")
        print(f"  Priorities:  {len(priorities)} (offline)")

    comp_col = COLUMNS.index("Components") + 1
    issuetype_col = COLUMNS.index("IssueType") + 1
    priority_col = COLUMNS.index("Priority") + 1
    status_col = COLUMNS.index("Status") + 1

    output_path = os.path.abspath(OUTPUT_FILE)

    print("Starte Excel...")
    excel = win32.gencache.EnsureDispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False

    try:
        wb = excel.Workbooks.Add()

        ws = wb.Worksheets(1)
        ws.Name = "Tickets"
        for col_idx, name in enumerate(COLUMNS, start=1):
            ws.Cells(1, col_idx).Value = name
        for col_idx, value in enumerate(EXAMPLE_ROW, start=1):
            ws.Cells(2, col_idx).Value = value

        listen = wb.Worksheets.Add(After=ws)
        listen.Name = "Listen"
        comp_formula = write_list(listen, 1, "Components", components)
        type_formula = write_list(listen, 2, "IssueTypes", issue_types)
        prio_formula = write_list(listen, 3, "Priorities", priorities)
        status_formula = write_list(listen, 4, "Status", STATUS_OPTIONS)

        add_validation(ws, comp_col, comp_formula)
        add_validation(ws, issuetype_col, type_formula)
        add_validation(ws, priority_col, prio_formula)
        add_validation(ws, status_col, status_formula)

        listen.Visible = XL_SHEET_HIDDEN

        try:
            module = wb.VBProject.VBComponents(ws.CodeName).CodeModule
            module.AddFromString(build_macro(comp_col))

            std_module = wb.VBProject.VBComponents.Add(VBEXT_CT_STD_MODULE)
            std_module.Name = "TicketMakros"
            std_module.CodeModule.AddFromString(build_button_module())
        except Exception as macro_error:
            excel.Quit()
            raise RuntimeError(
                "VBA-Makro konnte nicht eingefuegt werden. Bitte in Excel "
                "aktivieren: Datei > Optionen > Trust Center > Makroeinstellungen "
                "> 'Zugriff auf das VBA-Projektobjektmodell vertrauen'. "
                f"Originalfehler: {macro_error}"
            )

        # Button auf das Tickets-Blatt setzen (rechts neben der Tabelle)
        target = ws.Range("O1:Q2")
        button = ws.Buttons().Add(
            target.Left, target.Top, target.Width, target.Height
        )
        button.Name = "btnTicketsErstellen"
        button.Caption = "Tickets in Jira erstellen"
        button.OnAction = "Tickets_Erstellen"

        if os.path.exists(output_path):
            os.remove(output_path)
        wb.SaveAs(output_path, FileFormat=XL_OPENXML_MACRO)
        wb.Close(SaveChanges=False)
    finally:
        excel.Quit()

    print(f"\nFertig: {output_path}")
    print("Hinweis: Beim Oeffnen Makros zulassen. In der Components-Spalte")
    print("koennen per Dropdown mehrere Werte nacheinander gewaehlt werden.")
    print("Der Button 'Tickets in Jira erstellen' loest die Ticket-Erstellung aus")
    print("(fuehrt create_tickets.py aus). Test-/Echtlauf steuert DRY_RUN in der .env.")
    print("Setze in der .env EXCEL_FILE=tickets.xlsm")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\nAbbruch: {error}", file=sys.stderr)
        sys.exit(1)
