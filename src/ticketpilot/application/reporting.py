"""Read-only report orchestration and deterministic CSV export."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from ticketpilot.domain.reporting import ReportFilter, ReportMetrics, build_report

from .ports import JiraReadGateway


class ReportingService:
    def __init__(self, jira: JiraReadGateway) -> None:
        self._jira = jira

    def run(
        self,
        filters: ReportFilter | None = None,
        *,
        as_of: datetime | None = None,
    ) -> ReportMetrics:
        filters = filters or ReportFilter()
        tickets = tuple(self._jira.search_report_issues(filters))
        # Domain filtering is intentionally repeated.  It makes report results
        # reproducible even if a remote query backend applies a broader filter.
        return build_report(tickets, filters, as_of=as_of)

    def export_csv(self, report: ReportMetrics) -> str:
        """Export selected tickets as UTF-8-ready RFC-4180 CSV text."""

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            (
                "Jira Key",
                "Project",
                "Summary",
                "Issue Type",
                "Status",
                "Status Category",
                "Created",
                "Updated",
                "Due Date",
                "Assignee",
                "Reporter",
                "Priority",
                "Components",
                "Teams",
                "Sprint",
                "Epic",
                "Labels",
                "Impediment",
                "Story Points",
            )
        )
        for ticket in report.tickets:
            writer.writerow(
                _safe_csv_cell(value)
                for value in (
                    ticket.key,
                    ticket.project,
                    ticket.summary,
                    ticket.issue_type.value,
                    ticket.status,
                    ticket.status_category,
                    ticket.created.isoformat(),
                    ticket.updated.isoformat(),
                    ticket.due_date.isoformat() if ticket.due_date else "",
                    ticket.assignee or "",
                    ticket.reporter or "",
                    ticket.priority or "",
                    "; ".join(ticket.components),
                    "; ".join(ticket.teams),
                    ticket.sprint or "",
                    ticket.epic or "",
                    "; ".join(ticket.labels),
                    "Yes" if ticket.impediment else "No",
                    "" if ticket.story_points is None else ticket.story_points,
                )
            )
        return output.getvalue()


def _safe_csv_cell(value: object) -> object:
    """Prevent spreadsheet formula execution when CSV is opened in Excel."""

    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
