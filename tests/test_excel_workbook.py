from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from excel_workbook import (
    EXCEL_DATE_FORMAT,
    WORKSHEET_HEADERS,
    WorkbookSafetyError,
    apply_jira_key_hyperlinks,
    column_visibility_rows,
    create_workbook_backup,
    jira_browse_url,
    missing_ticket_columns,
    plan_workbook_migration,
    set_jira_key_hyperlink,
    workbook_mode,
)
from ticket_schema import ALL_TICKET_COLUMNS


class ExcelWorkbookTests(unittest.TestCase):
    def test_existing_xlsm_cannot_be_implicitly_overwritten(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tickets.xlsm"
            path.write_bytes(b"existing-data-and-vba")
            with self.assertRaisesRegex(WorkbookSafetyError, "nicht ueberschrieben"):
                workbook_mode(path, migrate_existing=False)
            self.assertEqual(workbook_mode(path, migrate_existing=True), "migrate")
            self.assertEqual(path.read_bytes(), b"existing-data-and-vba")

    def test_non_xlsm_target_is_rejected(self):
        with self.assertRaisesRegex(WorkbookSafetyError, "XLSM"):
            workbook_mode("tickets.xlsx", migrate_existing=False)

    def test_backup_is_byte_identical_and_collision_safe(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "tickets.xlsm"
            original = b"macro-package-bytes"
            source.write_bytes(original)
            timestamp = datetime(2026, 7, 21, 14, 35, 21)
            first = create_workbook_backup(source, timestamp=timestamp)
            second = create_workbook_backup(source, timestamp=timestamp)
            self.assertEqual(first.read_bytes(), original)
            self.assertEqual(second.read_bytes(), original)
            self.assertNotEqual(first, second)
            self.assertTrue(first.name.endswith(".xlsm"))

    def test_migration_plan_only_appends_missing_schema(self):
        existing = ("Summary", "Components", "Status")
        missing = missing_ticket_columns(existing)
        self.assertNotIn("Summary", missing)
        self.assertNotIn("Components", missing)
        self.assertEqual(missing[0], "Action")
        plan = plan_workbook_migration(existing, ("Tickets",))
        self.assertEqual(plan.missing_columns, missing)
        self.assertIn("Attachments", plan.missing_sheets)
        self.assertTrue(plan.has_changes)

    def test_hyperlink_displays_key_not_full_url(self):
        workbook = Workbook()
        cell = workbook.active["A1"]
        set_jira_key_hyperlink(cell, "https://jira.example.test/", "dah-4905")
        self.assertEqual(cell.value, "DAH-4905")
        self.assertEqual(
            cell.hyperlink.target, "https://jira.example.test/browse/DAH-4905"
        )
        self.assertEqual(cell.style, "Hyperlink")

    def test_hyperlinks_can_be_applied_to_existing_rows(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["JiraKey"])
        sheet.append(["DAH-1"])
        sheet.append([None])
        count = apply_jira_key_hyperlinks(sheet, {"JiraKey": 1}, "https://jira.test")
        self.assertEqual(count, 1)
        self.assertEqual(sheet["A2"].hyperlink.target, "https://jira.test/browse/DAH-1")

    def test_invalid_jira_key_is_not_turned_into_url(self):
        with self.assertRaisesRegex(ValueError, "Ungueltiger Jira Key"):
            jira_browse_url("https://jira.example.test", "not a key")

    def test_visibility_settings_cover_all_columns_without_deleting(self):
        rows = column_visibility_rows(("Action", "Summary"))
        self.assertEqual(len(rows), len(ALL_TICKET_COLUMNS))
        values = {row[1]: row[2] for row in rows}
        self.assertEqual(values["Action"], "yes")
        self.assertEqual(values["EpicName"], "no")
        self.assertTrue(all("loescht keine" in row[3] for row in rows))

    def test_prepared_sheets_have_attachment_and_link_rows(self):
        self.assertEqual(
            WORKSHEET_HEADERS["Attachments"][:3], ("RowId", "JiraKey", "FilePath")
        )
        self.assertIn("LinkType", WORKSHEET_HEADERS["Ticket_Links"])
        self.assertEqual(EXCEL_DATE_FORMAT, "dd.mm.yy")


if __name__ == "__main__":
    unittest.main()
