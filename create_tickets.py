"""
Automatische Jira-Ticketerstellung aus Excel.

Jira Server/Data Center 10.3.x
API: /rest/api/2
Auth: Bearer Token (Personal Access Token)
"""

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from openpyxl import load_workbook


REQUIRED_COLUMNS = [
    "Summary",
    "Description",
    "IssueType",
    "Priority",
    "Assignee",
    "Labels",
    "DueDate",
    "Status",
    "JiraKey",
    "ErrorMessage",
    "CreatedAt",
    "ExternalId",
]


def load_config() -> Dict[str, str]:
    load_dotenv()

    config = {
        "JIRA_URL": os.getenv("JIRA_URL", "").rstrip("/"),
        "JIRA_TOKEN": os.getenv("JIRA_TOKEN", ""),
        "PROJECT_KEY": os.getenv("PROJECT_KEY", ""),
        "EXCEL_FILE": os.getenv("EXCEL_FILE", "tickets.xlsx"),
        "DRY_RUN": os.getenv("DRY_RUN", "true").lower(),
    }

    missing = [
        key for key in ["JIRA_URL", "JIRA_TOKEN", "PROJECT_KEY"]
        if not config[key]
    ]
    if missing:
        raise ValueError(f"Fehlende .env-Werte: {', '.join(missing)}")

    return config


def jira_headers(config: Dict[str, str]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {config['JIRA_TOKEN']}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def test_jira_connection(config: Dict[str, str]) -> None:
    url = f"{config['JIRA_URL']}/rest/api/2/myself"
    response = requests.get(url, headers=jira_headers(config), timeout=30)

    if response.status_code != 200:
        raise RuntimeError(
            f"Jira-Verbindung fehlgeschlagen: {response.status_code} {response.text}"
        )

    user = response.json()
    print(f"  Verbunden als: {user.get('displayName', user.get('name', 'unbekannt'))}")


def test_project_access(config: Dict[str, str]) -> None:
    url = f"{config['JIRA_URL']}/rest/api/2/project/{config['PROJECT_KEY']}"
    response = requests.get(url, headers=jira_headers(config), timeout=30)

    if response.status_code != 200:
        raise RuntimeError(
            f"Projektzugriff fehlgeschlagen ({config['PROJECT_KEY']}): "
            f"{response.status_code} {response.text}"
        )

    project = response.json()
    print(f"  Projekt: {project.get('name', config['PROJECT_KEY'])}")


def parse_labels(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [label.strip() for label in str(value).split(",") if label.strip()]


def format_due_date(value) -> Optional[str]:
    if not value:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    value = str(value).strip()
    return value if value else None


def build_issue_payload(config: Dict[str, str], row: Dict[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {
        "project": {"key": config["PROJECT_KEY"]},
        "summary": str(row["Summary"]).strip(),
        "issuetype": {"name": str(row["IssueType"]).strip()},
    }

    description = row.get("Description")
    if description:
        fields["description"] = str(description).strip()

    priority = row.get("Priority")
    if priority:
        fields["priority"] = {"name": str(priority).strip()}

    assignee = row.get("Assignee")
    if assignee:
        fields["assignee"] = {"name": str(assignee).strip()}

    labels = parse_labels(row.get("Labels"))
    if labels:
        fields["labels"] = labels

    due_date = format_due_date(row.get("DueDate"))
    if due_date:
        fields["duedate"] = due_date

    return {"fields": fields}


def create_jira_issue(config: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{config['JIRA_URL']}/rest/api/2/issue"
    response = requests.post(
        url, headers=jira_headers(config), json=payload, timeout=30
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Jira-Erstellung fehlgeschlagen: {response.status_code} {response.text}"
        )

    return response.json()


def read_headers(sheet) -> Dict[str, int]:
    headers = {}
    for col_idx, cell in enumerate(sheet[1], start=1):
        if cell.value:
            headers[str(cell.value).strip()] = col_idx

    missing = [col for col in REQUIRED_COLUMNS if col not in headers]
    if missing:
        raise ValueError(f"Fehlende Excel-Spalten: {', '.join(missing)}")

    return headers


def get_row_values(sheet, row_idx: int, headers: Dict[str, int]) -> Dict[str, Any]:
    return {
        col_name: sheet.cell(row=row_idx, column=col_idx).value
        for col_name, col_idx in headers.items()
    }


def set_cell(sheet, row_idx: int, headers: Dict[str, int], column: str, value: Any) -> None:
    sheet.cell(row=row_idx, column=headers[column]).value = value


def validate_row(row: Dict[str, Any]) -> List[str]:
    errors = []
    if not row.get("Summary"):
        errors.append("Summary fehlt")
    if not row.get("IssueType"):
        errors.append("IssueType fehlt")
    if row.get("JiraKey"):
        errors.append("JiraKey bereits vorhanden (Duplikat)")
    return errors


def main() -> None:
    config = load_config()
    dry_run = config["DRY_RUN"] == "true"

    print("=" * 50)
    print("Jira Ticket-Erstellung aus Excel")
    print("=" * 50)
    print(f"  Jira-URL:    {config['JIRA_URL']}")
    print(f"  Projekt:     {config['PROJECT_KEY']}")
    print(f"  Excel:       {config['EXCEL_FILE']}")
    print(f"  Dry Run:     {dry_run}")
    print()

    print("Teste Jira-Verbindung...")
    test_jira_connection(config)

    print("Teste Projektzugriff...")
    test_project_access(config)
    print()

    workbook = load_workbook(config["EXCEL_FILE"])
    sheet = workbook.active
    headers = read_headers(sheet)

    processed = 0
    created = 0
    failed = 0
    skipped = 0

    for row_idx in range(2, sheet.max_row + 1):
        row = get_row_values(sheet, row_idx, headers)

        status = str(row.get("Status") or "").strip().upper()
        if status != "NEU":
            skipped += 1
            continue

        processed += 1

        try:
            validation_errors = validate_row(row)
            if validation_errors:
                raise ValueError("; ".join(validation_errors))

            payload = build_issue_payload(config, row)

            if dry_run:
                print(f"  [DRY RUN] Zeile {row_idx}: {row.get('Summary')}")
                continue

            result = create_jira_issue(config, payload)
            jira_key = result.get("key", "")

            set_cell(sheet, row_idx, headers, "Status", "ERSTELLT")
            set_cell(sheet, row_idx, headers, "JiraKey", jira_key)
            set_cell(sheet, row_idx, headers, "ErrorMessage", "")
            set_cell(sheet, row_idx, headers, "CreatedAt", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

            print(f"  [OK] Zeile {row_idx}: {jira_key}")
            created += 1

        except Exception as error:
            failed += 1
            set_cell(sheet, row_idx, headers, "Status", "FEHLER")
            set_cell(sheet, row_idx, headers, "ErrorMessage", str(error))
            print(f"  [FEHLER] Zeile {row_idx}: {error}")

    if not dry_run:
        workbook.save(config["EXCEL_FILE"])
        print(f"\nExcel gespeichert: {config['EXCEL_FILE']}")

    print()
    print("-" * 50)
    print(f"Verarbeitet:   {processed}")
    print(f"Erstellt:      {created}")
    print(f"Fehler:        {failed}")
    print(f"Übersprungen:  {skipped}")
    print(f"Dry Run:       {dry_run}")
    print("-" * 50)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\nAbbruch: {error}", file=sys.stderr)
        sys.exit(1)
