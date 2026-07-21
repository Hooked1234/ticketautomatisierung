import unittest

from ticket_schema import Action
from validators import (
    TicketValidationError,
    resolve_action,
    validate_jira_duration,
    validate_story_points,
    validate_ticket_row,
)


VALID_CREATE_ROWS = {
    "Epic": {
        "Action": "CREATE",
        "Project": "DAH",
        "IssueType": "Epic",
        "Summary": "Epic summary",
        "EpicName": "Epic name",
    },
    "Story": {
        "Action": "CREATE",
        "Project": "DAH",
        "IssueType": "Story",
        "Summary": "Story summary",
        "Components": "Backend",
    },
    "Bug": {
        "Action": "CREATE",
        "Project": "DAH",
        "IssueType": "Bug",
        "Summary": "Bug summary",
        "Components": "Frontend",
    },
    "Service Request": {
        "Action": "CREATE",
        "Project": "DAH",
        "IssueType": "Service Request",
        "Summary": "Request summary",
    },
    "Incident": {
        "Action": "CREATE",
        "Project": "DAH",
        "IssueType": "Incident",
        "Summary": "Incident summary",
    },
}


class ValidatorTests(unittest.TestCase):
    def test_actions_are_case_insensitive_but_restricted(self):
        self.assertIs(resolve_action({"Action": "create"}), Action.CREATE)
        self.assertIs(resolve_action({"Action": " UPDATE "}), Action.UPDATE)
        self.assertIs(resolve_action({"Action": "IGNORE"}), Action.IGNORE)
        with self.assertRaisesRegex(TicketValidationError, "Erlaubt"):
            resolve_action({"Action": "DELETE"})

    def test_legacy_status_values_are_compatible(self):
        self.assertIs(resolve_action({"Status": "NEU"}), Action.CREATE)
        self.assertIs(resolve_action({"Status": "EDIT"}), Action.UPDATE)
        self.assertIs(resolve_action({"Status": "ALT"}), Action.IGNORE)
        self.assertIs(resolve_action({"Status": "FEHLER"}), Action.IGNORE)

    def test_missing_action_is_clear(self):
        with self.assertRaisesRegex(TicketValidationError, "Action fehlt"):
            resolve_action({})

    def test_each_valid_issue_type_passes_exact_required_fields(self):
        for issue_type, row in VALID_CREATE_ROWS.items():
            with self.subTest(issue_type=issue_type):
                self.assertEqual(validate_ticket_row(row, ("DAH",)), [])

    def test_each_required_business_field_is_enforced(self):
        required = {
            "Epic": ("Summary", "EpicName"),
            "Story": ("Summary", "Components"),
            "Bug": ("Summary", "Components"),
            "Service Request": ("Summary",),
            "Incident": ("Summary",),
        }
        for issue_type, fields in required.items():
            for field in fields:
                row = dict(VALID_CREATE_ROWS[issue_type])
                row[field] = ""
                with self.subTest(issue_type=issue_type, field=field):
                    errors = validate_ticket_row(row, ("DAH",))
                    self.assertTrue(any("fehlt" in error for error in errors))

    def test_unknown_project_is_rejected(self):
        row = dict(VALID_CREATE_ROWS["Incident"], Project="OTHER")
        errors = validate_ticket_row(row, ("DAH",))
        self.assertTrue(any("nicht freigegeben" in error for error in errors))

    def test_project_is_required_for_new_schema_but_legacy_default_is_supported(self):
        row = dict(VALID_CREATE_ROWS["Incident"])
        row["Project"] = ""
        errors = validate_ticket_row(row, ("DAH",), default_project=None)
        self.assertTrue(any("Project fehlt" in error for error in errors))
        row.pop("Project")
        self.assertEqual(validate_ticket_row(row, ("DAH",), "DAH"), [])

    def test_create_rejects_existing_jira_key(self):
        row = dict(VALID_CREATE_ROWS["Incident"], JiraKey="DAH-123")
        errors = validate_ticket_row(row, ("DAH",))
        self.assertIn("Jira Key ist bei CREATE bereits gesetzt.", errors)

    def test_update_requires_valid_matching_key(self):
        row = {"Action": "UPDATE", "Project": "DAH", "JiraKey": "OTHER-1"}
        errors = validate_ticket_row(row, ("DAH",), "DAH")
        self.assertTrue(any("nicht vom Jira Key" in error for error in errors))

    def test_ignore_skips_other_validation(self):
        self.assertEqual(
            validate_ticket_row({"Action": "IGNORE", "Project": "OTHER"}, ("DAH",)),
            [],
        )

    def test_story_points_are_numeric(self):
        validate_story_points(3.5)
        validate_story_points("8")
        with self.assertRaisesRegex(TicketValidationError, "numerisch"):
            validate_story_points("large")

    def test_jira_time_formats(self):
        for value in ("30m", "2h", "3d", "4w", "1h 30m"):
            validate_jira_duration(value, "Estimate")
        with self.assertRaisesRegex(TicketValidationError, "Jira-Zeitformat"):
            validate_jira_duration("two hours", "Estimate")


if __name__ == "__main__":
    unittest.main()
