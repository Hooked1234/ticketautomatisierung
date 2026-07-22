from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from ticketpilot.domain import IssueType, ReportFilter, ReportTicket, build_report, filter_tickets

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


class ReportingDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tickets = (
            _ticket(
                "DAH-1",
                IssueType.STORY,
                "To Do",
                points=5,
                due=date(2026, 7, 20),
                assignee=None,
                sprint=None,
                component=("Analytics",),
                team=("BI",),
                labels=("pilot",),
                impediment=True,
                summary="Build dashboard",
            ),
            _ticket(
                "DAH-2",
                IssueType.BUG,
                "In Progress",
                points=3,
                due=date(2026, 7, 25),
                assignee="Bob",
                sprint="Sprint 7",
                component=("Platform",),
                team=("Core",),
                priority="Highest",
                summary="Repair pipeline",
            ),
            _ticket(
                "DAH-3",
                IssueType.STORY,
                "Done",
                points=8,
                due=date(2026, 7, 1),
                assignee="Alice",
                sprint="Sprint 7",
                component=("Analytics",),
                team=("BI",),
                epic="DAH-99",
                summary="Publish model",
            ),
            _ticket(
                "OPS-1",
                IssueType.INCIDENT,
                "Done",
                points=None,
                assignee=None,
                sprint=None,
                component=(),
                team=(),
                summary="Resolved outage",
                project="OPS",
            ),
        )

    def test_metrics_are_recomputed_from_selected_tickets(self) -> None:
        report = build_report(self.tickets, as_of=NOW)
        self.assertEqual((report.total, report.open, report.in_progress, report.done), (4, 1, 1, 2))
        self.assertEqual(report.completion_rate, 50.0)
        self.assertEqual(report.overdue, 1)
        self.assertEqual(report.due_date_exceeded, 1)
        self.assertEqual(report.without_assignee, 2)
        self.assertEqual(report.without_sprint, 2)
        self.assertEqual(report.impediments, 1)
        self.assertEqual(report.planned_story_points, 16.0)
        self.assertEqual(report.completed_story_points, 8.0)
        self.assertEqual(report.open_story_points, 8.0)
        self.assertEqual(report.by_issue_type["Story"], 2)
        self.assertEqual(report.by_component["Analytics"], 2)
        self.assertEqual(report.by_team["BI"], 2)
        self.assertEqual(report.by_sprint["Sprint 7"], 2)

    def test_empty_report_has_safe_completion_rate(self) -> None:
        report = build_report((), as_of=NOW)
        self.assertEqual(report.total, 0)
        self.assertEqual(report.completion_rate, 0.0)

    def test_epics_are_not_falsely_counted_as_missing_a_sprint(self) -> None:
        epic = _ticket(
            "DAH-9",
            IssueType.EPIC,
            "To Do",
            points=None,
            assignee="Alice",
            sprint=None,
            component=(),
            team=(),
            summary="Epic",
        )
        report = build_report((epic,), as_of=NOW)
        self.assertEqual(report.without_sprint, 0)

    def test_every_required_filter_dimension(self) -> None:
        cases = {
            "project": (ReportFilter(projects=frozenset({"OPS"})), {"OPS-1"}),
            "sprint": (ReportFilter(sprints=frozenset({"Sprint 7"})), {"DAH-2", "DAH-3"}),
            "period": (
                ReportFilter(date_from=date(2026, 7, 3), date_to=date(2026, 7, 4)),
                {"DAH-2", "DAH-3"},
            ),
            "status": (ReportFilter(statuses=frozenset({"Open"})), {"DAH-1"}),
            "category": (ReportFilter(status_categories=frozenset({"Done"})), {"DAH-3", "OPS-1"}),
            "type": (ReportFilter(issue_types=frozenset({IssueType.BUG})), {"DAH-2"}),
            "assignee": (ReportFilter(assignees=frozenset({"Bob"})), {"DAH-2"}),
            "reporter": (
                ReportFilter(reporters=frozenset({"Reporter"})),
                {"DAH-1", "DAH-2", "DAH-3", "OPS-1"},
            ),
            "priority": (ReportFilter(priorities=frozenset({"Highest"})), {"DAH-2"}),
            "component": (ReportFilter(components=frozenset({"Analytics"})), {"DAH-1", "DAH-3"}),
            "team": (ReportFilter(teams=frozenset({"Core"})), {"DAH-2"}),
            "epic": (ReportFilter(epics=frozenset({"DAH-99"})), {"DAH-3"}),
            "label": (ReportFilter(labels=frozenset({"pilot"})), {"DAH-1"}),
            "impediment": (ReportFilter(impediment=True), {"DAH-1"}),
            "key": (ReportFilter(jira_keys=frozenset({"DAH-2"})), {"DAH-2"}),
            "text": (ReportFilter(free_text="pipeline"), {"DAH-2"}),
        }
        for name, (filters, expected) in cases.items():
            with self.subTest(filter=name):
                self.assertEqual(
                    {ticket.key for ticket in filter_tickets(self.tickets, filters)}, expected
                )


def _ticket(
    key: str,
    issue_type: IssueType,
    category: str,
    *,
    points: float | None,
    assignee: str | None,
    sprint: str | None,
    component: tuple[str, ...],
    team: tuple[str, ...],
    summary: str,
    due: date | None = None,
    labels: tuple[str, ...] = (),
    impediment: bool = False,
    priority: str = "High",
    epic: str | None = None,
    project: str = "DAH",
) -> ReportTicket:
    number = int(key.split("-")[-1])
    created = datetime(2026, 7, min(number + 1, 28), tzinfo=UTC)
    return ReportTicket(
        key=key,
        project=project,
        summary=summary,
        description="Synthetic fixture",
        issue_type=issue_type,
        status="Open" if category == "To Do" else category,
        status_category=category,
        created=created,
        updated=created,
        due_date=due,
        assignee=assignee,
        reporter="Reporter",
        priority=priority,
        components=component,
        teams=team,
        sprint=sprint,
        epic=epic,
        labels=labels,
        impediment=impediment,
        story_points=points,
    )


if __name__ == "__main__":
    unittest.main()
