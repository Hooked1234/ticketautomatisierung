from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from ticketpilot.application import PreviewService, SyncService
from ticketpilot.application.errors import ConcurrencyConflict
from ticketpilot.application.ports import (
    JiraReadGateway,
    JiraWriteGateway,
    MetadataProvider,
    StaticProjectPolicy,
)
from ticketpilot.domain.models import (
    CLEAR_MARKER,
    Action,
    Attachment,
    IssueLink,
    IssueType,
    LinkDirection,
    SyncStatus,
    TicketCommand,
)
from ticketpilot.domain.reporting import ReportFilter
from ticketpilot.infrastructure.gateways import (
    DisabledJiraGateway,
    GatewayDisabledError,
    GatewayValidationError,
    LocalDemoGateway,
)


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class LocalDemoGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FixedClock()
        self.gateway = LocalDemoGateway(clock=self.clock)

    def test_gateway_matches_application_ports_and_is_explicitly_synthetic(self) -> None:
        self.assertIsInstance(self.gateway, JiraReadGateway)
        self.assertIsInstance(self.gateway, JiraWriteGateway)
        self.assertIsInstance(self.gateway, MetadataProvider)
        status = self.gateway.test_connection()
        self.assertTrue(status["ok"])
        self.assertTrue(status["synthetic"])
        self.assertEqual(status["mode"], "local-demo")

    def test_fetch_comments_and_read_only_reporting_use_typed_synthetic_data(self) -> None:
        issue = self.gateway.fetch_issue("dah-90001")
        comments = self.gateway.list_comments(
            issue.key,
            since=datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
        )
        report = self.gateway.search_report_issues(
            ReportFilter(issue_types=frozenset({IssueType.STORY}))
        )

        self.assertEqual(issue.issue_type, IssueType.STORY)
        self.assertEqual(issue.fields["sprint"], "demo:sprint:active")
        self.assertEqual(len(comments), 1)
        self.assertTrue(all(item.issue_type is IssueType.STORY for item in report))
        self.assertTrue(all(item.project == "DAH" for item in report))

    def test_demo_create_and_update_are_memory_only_with_concurrency_guard(self) -> None:
        created = self.gateway.create_issue(
            {
                "project": "DAH",
                "issue_type": "Story",
                "summary": "Lokaler Entwurf",
                "components": ["BI"],
                "description": "wird entfernt",
                "assignee": {"mode": "USER", "user_id": "alex.example"},
            }
        )
        self.assertTrue(created.issue_key.startswith("DAH-"))
        original = created.snapshot
        self.assertEqual(original.fields["assignee"], "alex.example")

        self.clock.value += timedelta(minutes=1)
        result = self.gateway.update_issue(
            created.issue_key,
            {"summary": "Geändert", "description": None, "priority": "High"},
            original.updated,
        )
        self.assertEqual(result.snapshot.fields["summary"], "Geändert")
        self.assertNotIn("description", result.snapshot.fields)

        self.clock.value += timedelta(minutes=1)
        clear_marker_result = self.gateway.update_issue(
            created.issue_key,
            {"priority": CLEAR_MARKER},
            result.snapshot.updated,
        )
        self.assertNotIn("priority", clear_marker_result.snapshot.fields)

        with self.assertRaises(ConcurrencyConflict):
            self.gateway.update_issue(
                created.issue_key,
                {"summary": "Veraltet"},
                original.updated,
            )
        with self.assertRaises(GatewayValidationError):
            self.gateway.update_issue(
                created.issue_key,
                {"reporter": "somebody.else"},
                result.snapshot.updated,
            )

    def test_attachments_links_metadata_and_dynamic_search(self) -> None:
        attachment = Attachment("memory://demo.pdf", "demo.pdf", "application/pdf")
        link = IssueLink(
            "demo:relates",
            "DAH-90002",
            LinkDirection.OUTWARD,
            "relates to",
        )
        self.gateway.upload_attachment("DAH-90001", attachment)
        self.gateway.add_issue_link("DAH-90001", link)

        context = self.gateway.get_context("DAH", IssueType.BUG, force_refresh=True)
        people = self.gateway.search("people", "alex")
        sprints = self.gateway.get_metadata("sprints")
        link_types = self.gateway.get_metadata("link_types")

        self.assertEqual(self.gateway.list_uploaded_attachments("DAH-90001"), (attachment,))
        self.assertEqual(self.gateway.list_added_links("DAH-90001"), (link,))
        self.assertIn("components", context.required_fields)
        self.assertEqual(people[0]["value"], "alex.example")
        self.assertEqual({item["state"] for item in sprints}, {"ACTIVE", "FUTURE"})
        self.assertEqual(
            {item["value"] for item in sprints},
            {"demo:sprint:active", "demo:sprint:future", "demo:sprint:service"},
        )
        self.assertEqual(
            {item["board_id"] for item in sprints},
            {"demo:board:1", "demo:board:2"},
        )
        self.assertEqual(
            {item["value"] for item in link_types},
            {"demo:blocks", "demo:relates"},
        )

        with self.assertRaises(GatewayValidationError):
            self.gateway.get_metadata("priorities", project="NOT_ALLOWED")

    def test_preview_to_sync_integration_translates_clear_to_jira_null(self) -> None:
        command = TicketCommand(
            row_id="clear-description",
            action=Action.UPDATE,
            project="DAH",
            issue_type=IssueType.STORY,
            summary="",
            jira_key="DAH-90001",
            fields={"description": CLEAR_MARKER},
        )
        preview_service = PreviewService(self.gateway, self.gateway)
        preview = preview_service.preview(command, dry_run=False)
        self.assertEqual(preview.payload, {"description": None})
        self.clock.value += timedelta(minutes=1)

        result = SyncService(
            self.gateway,
            self.gateway,
            StaticProjectPolicy(["DAH"]),
            preview_service=preview_service,
        ).execute(preview, confirmation_token=preview.confirmation_token)

        self.assertEqual(result.status, SyncStatus.UPDATED)
        self.assertNotIn("description", self.gateway.fetch_issue("DAH-90001").fields)

    def test_disabled_gateway_fails_closed_without_network(self) -> None:
        gateway = DisabledJiraGateway()
        self.assertFalse(gateway.test_connection()["connected"])
        with self.assertRaises(GatewayDisabledError):
            gateway.fetch_issue("DAH-1")
        with self.assertRaises(GatewayDisabledError):
            gateway.create_issue({"summary": "must not write"})


if __name__ == "__main__":
    unittest.main()
