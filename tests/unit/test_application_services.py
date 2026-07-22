from __future__ import annotations

import csv
import io
import unittest
from datetime import UTC, date, datetime, timedelta

from ticketpilot.application import (
    CommentService,
    MetadataCatalogService,
    MetadataContext,
    MetadataRefreshService,
    ReportingService,
    safe_error_message,
)
from ticketpilot.domain import Comment, IssueType, ReportFilter, ReportTicket

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


class ReadGateway:
    def __init__(self, tickets=(), comments=()) -> None:
        self.tickets = tickets
        self.comments = comments
        self.report_filters = []
        self.comment_calls = []

    def search_report_issues(self, filters):
        self.report_filters.append(filters)
        return self.tickets

    def list_comments(self, key, since=None):
        self.comment_calls.append((key, since))
        return self.comments

    def fetch_issue(self, key):
        raise NotImplementedError


class ReportingApplicationTests(unittest.TestCase):
    def test_service_applies_filters_even_if_gateway_returns_broad_data(self) -> None:
        tickets = (_report_ticket("DAH-1", "DAH"), _report_ticket("OPS-1", "OPS"))
        gateway = ReadGateway(tickets=tickets)
        filters = ReportFilter(projects=frozenset({"DAH"}))
        report = ReportingService(gateway).run(filters, as_of=NOW)
        self.assertEqual([ticket.key for ticket in report.tickets], ["DAH-1"])
        self.assertEqual(gateway.report_filters, [filters])

    def test_csv_export_is_reproducible_and_escaped(self) -> None:
        gateway = ReadGateway(tickets=(_report_ticket("DAH-1", "DAH", summary="A, B"),))
        service = ReportingService(gateway)
        report = service.run(as_of=NOW)
        csv_text = service.export_csv(report)
        rows = list(csv.reader(io.StringIO(csv_text)))
        self.assertEqual(rows[0][0:3], ["Jira Key", "Project", "Summary"])
        self.assertEqual(rows[1][0:3], ["DAH-1", "DAH", "A, B"])
        self.assertEqual(rows[1][-2:], ["No", "2.0"])

    def test_csv_export_neutralizes_spreadsheet_formula_injection(self) -> None:
        gateway = ReadGateway(
            tickets=(_report_ticket("DAH-1", "DAH", summary='=HYPERLINK("bad")'),)
        )
        report = ReportingService(gateway).run(as_of=NOW)
        rows = list(csv.reader(io.StringIO(ReportingService(gateway).export_csv(report))))
        self.assertEqual(rows[1][2], '\'=HYPERLINK("bad")')


class CommentApplicationTests(unittest.TestCase):
    def test_overview_is_read_only_sorted_and_counts_new_comments(self) -> None:
        old = Comment("1", "DAH-1", "Alice", NOW - timedelta(days=2), NOW, "Older")
        new = Comment("2", "DAH-1", "Bob", NOW, NOW, "Newer")
        gateway = ReadGateway(comments=(new, old))
        checked = NOW + timedelta(hours=1)
        overview = CommentService(gateway, clock=lambda: checked).overview(
            "DAH-1",
            since=NOW - timedelta(days=1),
        )
        self.assertEqual([comment.comment_id for comment in overview.comments], ["1", "2"])
        self.assertEqual(overview.new_comment_count, 1)
        self.assertEqual(overview.checked_at, checked)
        self.assertEqual(gateway.comment_calls, [("DAH-1", None)])
        self.assertFalse(hasattr(CommentService, "add_comment"))

    def test_naive_since_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            CommentService(ReadGateway()).overview("DAH-1", since=datetime(2026, 7, 21))


class MetadataApplicationTests(unittest.TestCase):
    def test_refresh_is_read_only_force_refresh_and_isolated(self) -> None:
        class Metadata:
            def __init__(self):
                self.calls = []

            def get_context(self, project, issue_type, *, force_refresh=False):
                self.calls.append((project, issue_type, force_refresh))
                if issue_type is IssueType.BUG:
                    raise TimeoutError("metadata timeout")
                return MetadataContext(project, issue_type, revision=issue_type.value)

        metadata = Metadata()
        results = MetadataRefreshService(metadata).refresh(
            "DAH", (IssueType.STORY, IssueType.BUG, IssueType.INCIDENT)
        )
        self.assertEqual([result.succeeded for result in results], [True, False, True])
        self.assertEqual(results[1].message, "Operation timed out.")
        self.assertTrue(all(call[2] for call in metadata.calls))

    def test_catalogue_and_large_search_are_explicitly_separated(self) -> None:
        class Metadata:
            def get_metadata(self, kind, *, project, force_refresh=False):
                self.catalogue_call = (kind, project, force_refresh)
                return ({"id": "10", "value": "High", "label": "High"},)

            def search(self, resource, query, *, project, start_at=0, limit=20):
                self.search_call = (resource, query, project, start_at, limit)
                return ({"id": "alice", "value": "alice", "label": "Alice"},)

        metadata = Metadata()
        service = MetadataCatalogService(metadata)
        options = service.catalogue("priorities", project="DAH", force_refresh=True)
        page = service.search("people", "ali", project="DAH", start_at=20, limit=10)
        self.assertEqual(options[0].option_id, "10")
        self.assertEqual(page.items[0].label, "Alice")
        self.assertEqual(metadata.catalogue_call, ("priorities", "DAH", True))
        self.assertEqual(metadata.search_call, ("people", "ali", "DAH", 20, 10))
        with self.assertRaises(ValueError):
            service.catalogue("people", project="DAH")
        with self.assertRaises(ValueError):
            service.search("priorities", "High", project="DAH")


class ErrorSanitizationTests(unittest.TestCase):
    def test_authorization_token_and_password_are_redacted_and_truncated(self) -> None:
        error = RuntimeError(
            "Authorization: Bearer abc.def token=token-secret password=hunter2 " + "x" * 500
        )
        message = safe_error_message(error, limit=120)
        for secret in ("abc.def", "token-secret", "hunter2"):
            self.assertNotIn(secret, message)
        self.assertIn("<redacted>", message)
        self.assertLessEqual(len(message), 120)


def _report_ticket(key: str, project: str, *, summary: str = "Synthetic") -> ReportTicket:
    return ReportTicket(
        key=key,
        project=project,
        summary=summary,
        issue_type=IssueType.STORY,
        status="Open",
        status_category="To Do",
        created=NOW,
        updated=NOW,
        due_date=date(2026, 7, 22),
        assignee="Alice",
        reporter="Reporter",
        priority="High",
        components=("Analytics",),
        teams=("BI",),
        sprint="Sprint 7",
        labels=("pilot",),
        story_points=2.0,
    )


if __name__ == "__main__":
    unittest.main()
