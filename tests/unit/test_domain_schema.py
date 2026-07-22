from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from ticketpilot.domain import (
    CLEAR_MARKER,
    Action,
    AssigneeMode,
    AssigneeSelection,
    Attachment,
    IssueLink,
    IssueType,
    LinkDirection,
    SprintRef,
    SprintState,
    TicketCommand,
    TicketSnapshot,
    ValidationContext,
    default_ticket_schema,
    effective_values,
    validate_command,
)
from ticketpilot.domain.models import is_blank
from ticketpilot.domain.schema import (
    COMPONENTS,
    EPIC_NAME,
    ISSUE_TYPE,
    PARTICIPANTS,
    PROJECT,
    SPRINT,
    SUMMARY,
)

NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)


class TicketSchemaTests(unittest.TestCase):
    def test_empty_frontend_collections_are_blank_values(self) -> None:
        self.assertTrue(is_blank([]))
        self.assertTrue(is_blank(()))
        self.assertTrue(is_blank(set()))
        self.assertFalse(is_blank(["BI"]))
        self.assertFalse(is_blank(False))

    def test_exact_central_required_fields_for_all_five_types(self) -> None:
        schema = default_ticket_schema()
        expected = {
            IssueType.EPIC: {PROJECT, ISSUE_TYPE, SUMMARY, EPIC_NAME},
            IssueType.STORY: {PROJECT, ISSUE_TYPE, SUMMARY, COMPONENTS},
            IssueType.BUG: {PROJECT, ISSUE_TYPE, SUMMARY, COMPONENTS},
            IssueType.SERVICE_REQUEST: {PROJECT, ISSUE_TYPE, SUMMARY},
            IssueType.INCIDENT: {PROJECT, ISSUE_TYPE, SUMMARY},
        }
        self.assertEqual(set(IssueType), set(expected))
        for issue_type, required in expected.items():
            with self.subTest(issue_type=issue_type):
                self.assertEqual(schema.required_fields(issue_type), frozenset(required))

    def test_participants_and_sprint_context_rules(self) -> None:
        schema = default_ticket_schema()
        self.assertTrue(schema.rule(PARTICIPANTS).allowed_for(IssueType.STORY))
        self.assertTrue(schema.rule(PARTICIPANTS).allowed_for(IssueType.BUG))
        self.assertFalse(schema.rule(PARTICIPANTS).allowed_for(IssueType.INCIDENT))
        self.assertFalse(schema.rule(SPRINT).allowed_for(IssueType.EPIC))
        for issue_type in (
            IssueType.STORY,
            IssueType.BUG,
            IssueType.SERVICE_REQUEST,
            IssueType.INCIDENT,
        ):
            self.assertTrue(schema.rule(SPRINT).allowed_for(issue_type))

    def test_each_issue_type_can_form_a_valid_create(self) -> None:
        fields_by_type = {
            IssueType.EPIC: {EPIC_NAME: "Modernisation"},
            IssueType.STORY: {COMPONENTS: ["Analytics"]},
            IssueType.BUG: {COMPONENTS: ["Platform"]},
            IssueType.SERVICE_REQUEST: {},
            IssueType.INCIDENT: {},
        }
        for index, (issue_type, fields) in enumerate(fields_by_type.items()):
            command = TicketCommand(
                row_id=str(index),
                action=Action.CREATE,
                project="DAH",
                issue_type=issue_type,
                summary="Synthetic test",
                fields=fields,
            )
            with self.subTest(issue_type=issue_type):
                self.assertEqual(validate_command(command), ())

    def test_update_empty_is_unchanged_and_exact_clear_deletes(self) -> None:
        snapshot = _story_snapshot()
        command = TicketCommand(
            row_id="r1",
            action=Action.UPDATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="",
            jira_key="DAH-1",
            fields={"description": CLEAR_MARKER, "priority": None},
        )
        values = effective_values(command, snapshot)
        self.assertEqual(values[SUMMARY], "Old summary")
        self.assertEqual(values["priority"], "High")
        self.assertIsNone(values["description"])
        self.assertEqual(validate_command(command, snapshot=snapshot), ())

    def test_only_exact_clear_marker_clears(self) -> None:
        snapshot = _story_snapshot()
        command = TicketCommand(
            row_id="r1",
            action=Action.UPDATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="",
            jira_key="DAH-1",
            fields={"description": " <CLEAR>"},
        )
        self.assertEqual(effective_values(command, snapshot)["description"], " <CLEAR>")

    def test_required_field_cannot_be_cleared(self) -> None:
        snapshot = _story_snapshot()
        command = TicketCommand(
            row_id="r1",
            action=Action.UPDATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary=CLEAR_MARKER,
            jira_key="DAH-1",
        )
        codes = {message.code for message in validate_command(command, snapshot=snapshot)}
        self.assertIn("REQUIRED_FIELD_CANNOT_CLEAR", codes)
        self.assertIn("REQUIRED_FIELD", codes)

    def test_protected_fields_and_identity_mismatches_are_rejected(self) -> None:
        snapshot = _story_snapshot()
        command = TicketCommand(
            row_id="r1",
            action=Action.UPDATE,
            project="OTHER",
            issue_type=IssueType.STORY,
            summary="",
            jira_key="DAH-2",
            fields={"reporter": "somebody", "status": "Done"},
        )
        codes = {message.code for message in validate_command(command, snapshot=snapshot)}
        self.assertIn("PROJECT_IMMUTABLE", codes)
        self.assertIn("JIRA_KEY_IMMUTABLE", codes)
        self.assertIn("IMMUTABLE_FIELD", codes)

    def test_jira_metadata_adds_required_fields(self) -> None:
        command = TicketCommand(
            row_id="r1",
            action=Action.CREATE,
            project="DAH",
            issue_type=IssueType.INCIDENT,
            summary="Incident",
        )
        messages = validate_command(
            command,
            context=ValidationContext(jira_required_fields=frozenset({"priority"})),
        )
        self.assertEqual(
            [(message.code, message.field) for message in messages],
            [("REQUIRED_FIELD", "priority")],
        )

    def test_metadata_unavailable_optional_field_warns(self) -> None:
        command = TicketCommand(
            row_id="r1",
            action=Action.CREATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="Story",
            fields={COMPONENTS: ["Data"], "story_points": 3},
        )
        messages = validate_command(
            command,
            context=ValidationContext(available_fields=frozenset()),
        )
        self.assertEqual(
            [(message.code, message.field, message.severity.value) for message in messages],
            [("FIELD_UNAVAILABLE", "story_points", "WARNING")],
        )

    def test_dates_story_points_and_time_tracking_are_typed(self) -> None:
        valid = TicketCommand(
            row_id="r1",
            action=Action.CREATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="Story",
            fields={
                COMPONENTS: ["Data"],
                "start_date": date(2026, 7, 21),
                "story_points": 5.5,
                "original_estimate": "2h 30m",
                "impediment": False,
            },
        )
        self.assertEqual(validate_command(valid), ())
        invalid = TicketCommand(
            row_id="r2",
            action=Action.CREATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="Story",
            fields={
                COMPONENTS: ["Data"],
                "start_date": "21.07.26",
                "story_points": "five",
                "original_estimate": "two hours",
                "impediment": "Yes",
            },
        )
        codes = {message.code for message in validate_command(invalid)}
        self.assertEqual(
            codes,
            {"INVALID_DATE", "INVALID_NUMBER", "INVALID_TIME_ESTIMATE", "INVALID_BOOLEAN"},
        )

    def test_assignee_supports_unassigned_self_and_resolved_assignable_user(self) -> None:
        values = (
            AssigneeSelection(AssigneeMode.UNASSIGNED),
            AssigneeSelection(AssigneeMode.SELF),
            AssigneeSelection(AssigneeMode.USER, user_id="alice", display_name="Alice"),
        )
        for index, assignee in enumerate(values):
            command = TicketCommand(
                row_id=f"r{index}",
                action=Action.CREATE,
                project="DAH",
                issue_type=IssueType.INCIDENT,
                summary="Incident",
                fields={"assignee": assignee},
            )
            with self.subTest(mode=assignee.mode):
                self.assertEqual(validate_command(command), ())
        invalid = TicketCommand(
            "bad",
            Action.CREATE,
            "DAH",
            IssueType.INCIDENT,
            "Incident",
            fields={"assignee": AssigneeSelection(AssigneeMode.USER)},
        )
        self.assertIn("ASSIGNEE_USER_REQUIRED", {m.code for m in validate_command(invalid)})

    def test_epic_and_closed_sprint_are_rejected(self) -> None:
        sprint = SprintRef("42", "Sprint 42", SprintState.CLOSED, board_id="3")
        command = TicketCommand(
            row_id="r1",
            action=Action.CREATE,
            project="DAH",
            issue_type=IssueType.EPIC,
            summary="Epic",
            fields={EPIC_NAME: "Epic"},
            sprint=sprint,
        )
        codes = {message.code for message in validate_command(command)}
        self.assertIn("FIELD_NOT_ALLOWED", codes)
        self.assertIn("EPIC_SPRINT_FORBIDDEN", codes)
        self.assertIn("CLOSED_SPRINT_FORBIDDEN", codes)

    def test_duplicate_attachments_and_links_are_row_errors(self) -> None:
        attachment = Attachment("/tmp/a.txt")
        link = IssueLink("10001", "DAH-2", LinkDirection.OUTWARD)
        command = TicketCommand(
            row_id="r1",
            action=Action.CREATE,
            project="DAH",
            issue_type=IssueType.INCIDENT,
            summary="Incident",
            attachments=(attachment, attachment),
            links=(link, link),
        )
        codes = {message.code for message in validate_command(command)}
        self.assertEqual(codes, {"DUPLICATE_ATTACHMENT", "DUPLICATE_LINK"})


def _story_snapshot() -> TicketSnapshot:
    return TicketSnapshot(
        key="DAH-1",
        project="DAH",
        issue_type=IssueType.STORY,
        reporter="alice",
        status="Open",
        updated=NOW,
        fields={
            SUMMARY: "Old summary",
            COMPONENTS: ["Analytics"],
            "description": "Old description",
            "priority": "High",
        },
    )


if __name__ == "__main__":
    unittest.main()
