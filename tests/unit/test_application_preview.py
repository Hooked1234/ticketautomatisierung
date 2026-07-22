from __future__ import annotations

import unittest
from datetime import UTC, datetime

from ticketpilot.application import MetadataContext, PreviewService
from ticketpilot.domain import (
    CLEAR_MARKER,
    Action,
    AssigneeMode,
    AssigneeSelection,
    ChangeKind,
    IssueType,
    SprintRef,
    SprintState,
    TicketCommand,
    TicketSnapshot,
)
from ticketpilot.domain.schema import default_ticket_schema

NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)


class FakeMetadata:
    def __init__(self, available_fields=None, required_fields=frozenset()) -> None:
        self.available_fields = available_fields
        self.required_fields = required_fields
        self.calls = []

    def get_context(self, project, issue_type, *, force_refresh=False):
        self.calls.append((project, issue_type, force_refresh))
        schema = default_ticket_schema()
        candidates = self.available_fields or schema.allowed_fields(issue_type)
        field_ids = {
            name: f"synthetic:{name}"
            for name in candidates
            if schema.rule(name) and schema.rule(name).metadata_controlled
        }
        return MetadataContext(
            project=project,
            issue_type=issue_type,
            available_fields=self.available_fields,
            required_fields=self.required_fields,
            field_ids=field_ids,
        )


class FakeJira:
    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot
        self.fetches = []

    def fetch_issue(self, key):
        self.fetches.append(key)
        if self.snapshot is None:
            raise LookupError("not found")
        return self.snapshot


class PreviewServiceTests(unittest.TestCase):
    def test_create_preview_contains_field_diff_and_no_reporter(self) -> None:
        service = PreviewService(FakeJira(), FakeMetadata())
        command = TicketCommand(
            row_id="r1",
            action=Action.CREATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="New story",
            fields={"components": ["Analytics"], "reporter": None, "description": "Text"},
            sprint=SprintRef("88", "Sprint 8", SprintState.ACTIVE, "4"),
        )
        preview = service.preview(command, dry_run=False)
        self.assertTrue(preview.is_valid)
        self.assertIsNotNone(preview.confirmation_token)
        self.assertEqual(preview.payload["sprint"], "88")
        self.assertNotIn("reporter", preview.payload)
        self.assertEqual({change.field for change in preview.changes}, set(preview.payload))
        self.assertTrue(all(change.kind is ChangeKind.SET for change in preview.changes))

    def test_assignee_modes_are_canonicalized_for_adapter_boundary(self) -> None:
        service = PreviewService(FakeJira(), FakeMetadata())
        self_selection = TicketCommand(
            "r1",
            Action.CREATE,
            "DAH",
            IssueType.INCIDENT,
            "Incident",
            fields={"assignee": AssigneeSelection(AssigneeMode.SELF)},
        )
        user_selection = TicketCommand(
            "r2",
            Action.CREATE,
            "DAH",
            IssueType.INCIDENT,
            "Incident",
            fields={"assignee": AssigneeSelection(AssigneeMode.USER, "alice")},
        )
        unassigned = TicketCommand(
            "r3",
            Action.CREATE,
            "DAH",
            IssueType.INCIDENT,
            "Incident",
            fields={"assignee": AssigneeSelection(AssigneeMode.UNASSIGNED)},
        )
        self.assertEqual(service.preview(self_selection).payload["assignee"], {"mode": "SELF"})
        self.assertEqual(
            service.preview(user_selection).payload["assignee"],
            {"mode": "USER", "user_id": "alice"},
        )
        self.assertIsNone(service.preview(unassigned).payload["assignee"])

    def test_dry_run_preview_never_issues_confirmation_token(self) -> None:
        service = PreviewService(FakeJira(), FakeMetadata())
        preview = service.preview(_create_incident(), dry_run=True)
        self.assertTrue(preview.is_valid)
        self.assertIsNone(preview.confirmation_token)

    def test_update_diff_implements_blank_unchanged_and_clear(self) -> None:
        snapshot = _snapshot()
        service = PreviewService(FakeJira(snapshot), FakeMetadata())
        command = TicketCommand(
            row_id="r1",
            action=Action.UPDATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="",
            jira_key="DAH-1",
            fields={"description": CLEAR_MARKER, "priority": None, "labels": ["new"]},
        )
        preview = service.preview(command, dry_run=False)
        self.assertTrue(preview.is_valid)
        self.assertEqual(preview.payload, {"description": None, "labels": ["new"]})
        changes = {change.field: change for change in preview.changes}
        self.assertEqual(changes["description"].kind, ChangeKind.CLEAR)
        self.assertEqual(changes["labels"].before, ["old"])
        self.assertNotIn("priority", changes)
        self.assertNotIn("summary", changes)

    def test_resolved_user_matches_jira_snapshot_identity_without_false_diff(self) -> None:
        snapshot = TicketSnapshot(
            key="DAH-1",
            project="DAH",
            issue_type=IssueType.STORY,
            reporter="demo.user",
            status="Open",
            updated=NOW,
            fields={
                "summary": "Story",
                "components": ["BI"],
                "assignee": "alex.example",
            },
        )
        command = TicketCommand(
            row_id="r1",
            action=Action.UPDATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="",
            jira_key="DAH-1",
            fields={"assignee": AssigneeSelection(AssigneeMode.USER, "alex.example")},
        )

        preview = PreviewService(FakeJira(snapshot), FakeMetadata()).preview(command)

        self.assertTrue(preview.is_valid)
        self.assertNotIn("assignee", preview.payload)

    def test_non_exact_clear_is_a_literal_set(self) -> None:
        service = PreviewService(FakeJira(_snapshot()), FakeMetadata())
        command = TicketCommand(
            row_id="r1",
            action=Action.UPDATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="",
            jira_key="DAH-1",
            fields={"description": "<CLEAR> "},
        )
        preview = service.preview(command)
        self.assertEqual(preview.payload, {"description": "<CLEAR> "})
        self.assertEqual(preview.changes[0].kind, ChangeKind.SET)

    def test_optional_field_unavailable_in_context_is_not_transmitted(self) -> None:
        metadata = FakeMetadata(available_fields=frozenset())
        service = PreviewService(FakeJira(), metadata)
        command = TicketCommand(
            row_id="r1",
            action=Action.CREATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="Story",
            fields={"components": ["Data"], "story_points": 3},
        )
        preview = service.preview(command)
        self.assertTrue(preview.is_valid)
        self.assertNotIn("story_points", preview.payload)
        self.assertEqual(
            [(m.code, m.field) for m in preview.warnings], [("FIELD_UNAVAILABLE", "story_points")]
        )

    def test_ambiguous_or_missing_custom_field_id_blocks_preview(self) -> None:
        class UnresolvedMetadata(FakeMetadata):
            def get_context(self, project, issue_type, *, force_refresh=False):
                return MetadataContext(
                    project,
                    issue_type,
                    available_fields=frozenset({"story_points"}),
                    field_ids={},
                )

        command = TicketCommand(
            "r1",
            Action.CREATE,
            "DAH",
            IssueType.STORY,
            "Story",
            fields={"components": ["Data"], "story_points": 3},
        )
        preview = PreviewService(FakeJira(), UnresolvedMetadata()).preview(command)
        self.assertFalse(preview.is_valid)
        self.assertIn("FIELD_ID_UNRESOLVED", {message.code for message in preview.errors})

    def test_update_loads_snapshot_and_rejects_protected_change(self) -> None:
        jira = FakeJira(_snapshot())
        service = PreviewService(jira, FakeMetadata())
        command = TicketCommand(
            row_id="r1",
            action=Action.UPDATE,
            project="OTHER",
            issue_type=IssueType.STORY,
            summary="",
            jira_key="DAH-1",
        )
        preview = service.preview(command)
        self.assertFalse(preview.is_valid)
        self.assertIn("PROJECT_IMMUTABLE", {message.code for message in preview.errors})
        self.assertEqual(jira.fetches, ["DAH-1"])

    def test_ignore_does_not_touch_jira_or_metadata(self) -> None:
        jira = FakeJira()
        metadata = FakeMetadata()
        service = PreviewService(jira, metadata)
        command = TicketCommand("r1", Action.IGNORE, "", IssueType.INCIDENT)
        preview = service.preview(command, dry_run=False)
        self.assertTrue(preview.is_valid)
        self.assertEqual(jira.fetches, [])
        self.assertEqual(metadata.calls, [])
        self.assertIsNone(preview.confirmation_token)

    def test_preview_many_isolates_unexpected_row_failure(self) -> None:
        class ExplodingPreview(PreviewService):
            def preview(self, command, *, dry_run=True):
                if command.row_id == "bad":
                    raise RuntimeError("bad row")
                return super().preview(command, dry_run=dry_run)

        service = ExplodingPreview(FakeJira(), FakeMetadata())
        previews = service.preview_many(
            (
                _create_incident("good"),
                _create_incident("bad"),
                _create_incident("last"),
            )
        )
        self.assertEqual([preview.is_valid for preview in previews], [True, False, True])
        self.assertEqual(previews[1].errors[0].code, "ROW_PREVIEW_FAILED")

    def test_preview_load_errors_are_sanitized(self) -> None:
        class BadMetadata(FakeMetadata):
            def get_context(self, *args, **kwargs):
                raise RuntimeError("Authorization: Bearer top-secret token=also-secret")

        preview = PreviewService(FakeJira(), BadMetadata()).preview(_create_incident())
        rendered = preview.errors[0].message
        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("also-secret", rendered)
        self.assertIn("<redacted>", rendered)


def _create_incident(row_id="r1") -> TicketCommand:
    return TicketCommand(
        row_id=row_id,
        action=Action.CREATE,
        project="DAH",
        issue_type=IssueType.INCIDENT,
        summary="Synthetic incident",
    )


def _snapshot() -> TicketSnapshot:
    return TicketSnapshot(
        key="DAH-1",
        project="DAH",
        issue_type=IssueType.STORY,
        reporter="alice",
        status="Open",
        updated=NOW,
        fields={
            "summary": "Old",
            "components": ["Data"],
            "description": "Old text",
            "priority": "High",
            "labels": ["old"],
        },
    )


if __name__ == "__main__":
    unittest.main()
