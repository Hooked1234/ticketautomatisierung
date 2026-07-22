"""Read-only Jira comment overview use case."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ticketpilot.domain.models import CommentOverview

from .ports import JiraReadGateway


class CommentService:
    def __init__(
        self,
        jira: JiraReadGateway,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._jira = jira
        self._clock = clock or (lambda: datetime.now(UTC))

    def overview(
        self,
        issue_key: str,
        *,
        since: datetime | None = None,
    ) -> CommentOverview:
        if not issue_key.strip():
            raise ValueError("issue_key is required")
        if since is not None and since.tzinfo is None:
            raise ValueError("since must be timezone-aware")
        comments = tuple(
            sorted(
                self._jira.list_comments(issue_key.strip(), since=None),
                key=lambda comment: (comment.created, comment.comment_id),
            )
        )
        new_count = (
            sum(comment.created > since for comment in comments)
            if since is not None
            else len(comments)
        )
        return CommentOverview(
            issue_key=issue_key.strip(),
            checked_at=self._clock(),
            comments=comments,
            new_comment_count=new_count,
        )
