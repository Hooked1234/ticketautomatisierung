from pathlib import Path
import unittest

import yaml

from ticket_schema import (
    DEFAULT_VISIBLE_COLUMNS,
    FIELD_DEFINITIONS,
    ISSUE_TYPE_SCHEMAS,
    SUPPORTED_ACTIONS,
    SUPPORTED_ISSUE_TYPES,
)


ROOT = Path(__file__).resolve().parents[1]


class TicketSchemaTests(unittest.TestCase):
    def test_all_five_issue_types_are_centralized(self):
        self.assertEqual(
            SUPPORTED_ISSUE_TYPES,
            ("Epic", "Story", "Bug", "Service Request", "Incident"),
        )

    def test_required_fields_are_exact(self):
        expected = {
            "Epic": ("Project", "IssueType", "Summary", "EpicName"),
            "Story": ("Project", "IssueType", "Summary", "Components"),
            "Bug": ("Project", "IssueType", "Summary", "Components"),
            "Service Request": ("Project", "IssueType", "Summary"),
            "Incident": ("Project", "IssueType", "Summary"),
        }
        self.assertEqual(
            {name: schema.required_fields for name, schema in ISSUE_TYPE_SCHEMAS.items()},
            expected,
        )

    def test_supported_actions_are_exact(self):
        self.assertEqual(SUPPORTED_ACTIONS, ("CREATE", "UPDATE", "IGNORE"))

    def test_only_non_epics_support_sprints(self):
        self.assertFalse(ISSUE_TYPE_SCHEMAS["Epic"].supports_sprint)
        for issue_type in ("Story", "Bug", "Service Request", "Incident"):
            self.assertTrue(ISSUE_TYPE_SCHEMAS[issue_type].supports_sprint)

    def test_participants_are_not_offered_for_service_request_or_incident(self):
        self.assertIn("Participants", ISSUE_TYPE_SCHEMAS["Story"].optional_fields)
        self.assertIn("Participants", ISSUE_TYPE_SCHEMAS["Bug"].optional_fields)
        self.assertNotIn(
            "Participants", ISSUE_TYPE_SCHEMAS["Service Request"].optional_fields
        )
        self.assertNotIn("Participants", ISSUE_TYPE_SCHEMAS["Incident"].optional_fields)

    def test_custom_fields_have_no_hard_coded_jira_id(self):
        custom_fields = [field for field in FIELD_DEFINITIONS.values() if field.custom_field]
        self.assertTrue(custom_fields)
        self.assertTrue(all(field.jira_field is None for field in custom_fields))

    def test_attachments_and_links_use_separate_sheets(self):
        self.assertFalse(FIELD_DEFINITIONS["Attachment"].worksheet_column)
        self.assertFalse(FIELD_DEFINITIONS["LinkedIssues"].worksheet_column)

    def test_yaml_visible_columns_match_central_defaults(self):
        raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(
            tuple(raw["excel"]["default_visible_columns"]), DEFAULT_VISIBLE_COLUMNS
        )


if __name__ == "__main__":
    unittest.main()
