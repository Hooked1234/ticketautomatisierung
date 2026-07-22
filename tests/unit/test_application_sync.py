from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ticketpilot.application import (
    ConcurrencyConflict,
    DefiniteWriteError,
    MemoryTicketRepository,
    MetadataContext,
    PreviewService,
    StaticProjectPolicy,
    SyncService,
)
from ticketpilot.domain import (
    Action,
    Attachment,
    CreateResult,
    IssueLink,
    IssueType,
    LinkDirection,
    Severity,
    SyncStatus,
    TicketCommand,
    TicketSnapshot,
    ValidationMessage,
    WriteResult,
)
from ticketpilot.domain.schema import default_ticket_schema

NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)


class FakeMetadata:
    def __init__(self, available_fields=None) -> None:
        self.available_fields = available_fields
        self.force_available_fields = available_fields
        self.calls = []
        self.fail_force = None

    def get_context(self, project, issue_type, *, force_refresh=False):
        self.calls.append((project, issue_type, force_refresh))
        if force_refresh and self.fail_force:
            raise self.fail_force
        available = self.force_available_fields if force_refresh else self.available_fields
        schema = default_ticket_schema()
        candidates = available or schema.allowed_fields(issue_type)
        field_ids = {
            name: f"synthetic:{name}"
            for name in candidates
            if schema.rule(name) and schema.rule(name).metadata_controlled
        }
        return MetadataContext(
            project,
            issue_type,
            available_fields=available,
            field_ids=field_ids,
        )


class RecordingRepository(MemoryTicketRepository):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def save_jira_key(self, row_id, issue_key):
        self.events.append(("save_key", issue_key))
        super().save_jira_key(row_id, issue_key)

    def save_result(self, result):
        self.events.append(("save_result", result.status))
        super().save_result(result)


class RecordingAudit:
    def __init__(self) -> None:
        self.events = []

    def record(self, event) -> None:
        self.events.append(event)


class FakeJira:
    def __init__(self, snapshot=None, events=None) -> None:
        self.snapshot = snapshot
        self.events = events if events is not None else []
        self.fetch_calls = []
        self.create_calls = []
        self.update_calls = []
        self.attachment_calls = []
        self.link_calls = []
        self.create_error = None
        self.update_error = None
        self.attachment_errors = set()
        self.link_errors = set()
        self.next_key = "DAH-101"

    def fetch_issue(self, key):
        self.fetch_calls.append(key)
        if self.snapshot is None:
            raise LookupError("missing")
        return self.snapshot

    def create_issue(self, payload):
        self.events.append(("create", payload.get("summary")))
        self.create_calls.append(dict(payload))
        if self.create_error:
            raise self.create_error
        return CreateResult(self.next_key)

    def update_issue(self, key, payload, expected_updated):
        self.events.append(("update", key))
        self.update_calls.append((key, dict(payload), expected_updated))
        if self.update_error:
            raise self.update_error
        return WriteResult(self.snapshot)

    def upload_attachment(self, key, attachment):
        self.events.append(("attachment", attachment.reference))
        self.attachment_calls.append((key, attachment.reference))
        if attachment.reference in self.attachment_errors:
            raise OSError("upload failed")

    def add_issue_link(self, key, link):
        self.events.append(("link", link.target_key))
        self.link_calls.append((key, link.target_key))
        if link.target_key in self.link_errors:
            raise OSError("link failed")

    def search_report_issues(self, filters):
        return ()

    def list_comments(self, key, since=None):
        return ()


class SyncServiceTests(unittest.TestCase):
    def test_dry_run_never_calls_a_write(self) -> None:
        jira = FakeJira()
        metadata = FakeMetadata()
        preview = PreviewService(jira, metadata).preview(_create(), dry_run=True)
        service = SyncService(jira, metadata, StaticProjectPolicy(("DAH",)))
        result = service.execute(preview)
        self.assertEqual(result.status, SyncStatus.DRY_RUN)
        self.assertEqual(jira.create_calls, [])
        self.assertNotIn(True, [call[2] for call in metadata.calls])

    def test_live_write_requires_matching_preview_confirmation(self) -> None:
        jira = FakeJira()
        metadata = FakeMetadata()
        preview = PreviewService(jira, metadata).preview(_create(), dry_run=False)
        service = SyncService(jira, metadata, StaticProjectPolicy(("DAH",)))
        result = service.execute(preview, confirmation_token="wrong")
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("confirmation", result.message.lower())
        self.assertEqual(jira.create_calls, [])

    def test_preview_payload_mutation_after_review_is_detected(self) -> None:
        jira = FakeJira()
        metadata = FakeMetadata()
        mutable_components = ["Data"]
        command = TicketCommand(
            "r1",
            Action.CREATE,
            "DAH",
            IssueType.STORY,
            "Story",
            fields={"components": mutable_components},
        )
        preview = PreviewService(jira, metadata).preview(command, dry_run=False)
        mutable_components.append("Unreviewed")
        result = SyncService(jira, metadata, StaticProjectPolicy(("DAH",))).execute(
            preview,
            confirmation_token=preview.confirmation_token,
        )
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("changed after review", result.message)
        self.assertEqual(jira.create_calls, [])

    def test_allowlist_is_a_non_bypassable_write_gate(self) -> None:
        jira = FakeJira()
        metadata = FakeMetadata()
        preview = PreviewService(jira, metadata).preview(_create(), dry_run=False)
        service = SyncService(jira, metadata, StaticProjectPolicy(("OTHER",)))
        result = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("allowlist", result.message)
        self.assertEqual(jira.create_calls, [])

    def test_create_saves_key_before_independent_attachment_and_link_work(self) -> None:
        events = []
        jira = FakeJira(events=events)
        jira.attachment_errors.add("bad.txt")
        metadata = FakeMetadata()
        repository = RecordingRepository(events)
        command = _create(
            attachments=(Attachment("bad.txt"), Attachment("good.txt")),
            links=(IssueLink("100", "DAH-2", LinkDirection.OUTWARD),),
        )
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(command, dry_run=False)
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            repository=repository,
            preview_service=preview_service,
        )
        result = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(result.status, SyncStatus.CREATED)
        self.assertEqual(result.issue_key, "DAH-101")
        self.assertEqual(repository.jira_keys["r1"], "DAH-101")
        self.assertLess(
            events.index(("save_key", "DAH-101")), events.index(("attachment", "bad.txt"))
        )
        self.assertEqual([item.succeeded for item in result.related], [False, True, True])
        self.assertEqual(len(jira.create_calls), 1)
        self.assertIn(True, [call[2] for call in metadata.calls])

    def test_ambiguous_create_is_never_blindly_retried(self) -> None:
        jira = FakeJira()
        jira.create_error = TimeoutError("timed out after POST")
        metadata = FakeMetadata()
        repository = MemoryTicketRepository()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(_create(), dry_run=False)
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            repository=repository,
            preview_service=preview_service,
        )
        first = service.execute(preview, confirmation_token=preview.confirmation_token)
        second = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(first.status, SyncStatus.UNCERTAIN)
        self.assertEqual(second.status, SyncStatus.UNCERTAIN)
        self.assertIn("reconcile", second.message)
        self.assertEqual(len(jira.create_calls), 1)

        restarted = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            repository=repository,
            preview_service=preview_service,
        )
        fresh = preview_service.preview(_create(), dry_run=False)
        after_restart = restarted.execute(
            fresh,
            confirmation_token=fresh.confirmation_token,
        )
        self.assertEqual(after_restart.status, SyncStatus.UNCERTAIN)
        self.assertEqual(len(jira.create_calls), 1)

    def test_malformed_or_cross_project_create_key_is_uncertain_without_additions(self) -> None:
        for key in ("OPS-123", "DAH-X", " DAH-123 ", "DAH-123/extra"):
            with self.subTest(key=key):
                jira = FakeJira()
                jira.next_key = key
                metadata = FakeMetadata()
                repository = MemoryTicketRepository()
                command = _create(
                    attachments=(Attachment("never-upload.txt"),),
                    links=(IssueLink("100", "DAH-2"),),
                )
                preview_service = PreviewService(jira, metadata)
                preview = preview_service.preview(command, dry_run=False)
                result = SyncService(
                    jira,
                    metadata,
                    StaticProjectPolicy(("DAH",)),
                    repository=repository,
                    preview_service=preview_service,
                ).execute(preview, confirmation_token=preview.confirmation_token)
                self.assertEqual(result.status, SyncStatus.UNCERTAIN)
                self.assertEqual(result.action, Action.CREATE)
                self.assertIsNone(result.issue_key)
                self.assertEqual(repository.jira_keys, {})
                self.assertEqual(jira.attachment_calls, [])
                self.assertEqual(jira.link_calls, [])

    def test_definite_create_failure_still_requires_a_fresh_confirmed_preview(self) -> None:
        jira = FakeJira()
        jira.create_error = DefiniteWriteError("rejected")
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(_create(), dry_run=False)
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            preview_service=preview_service,
        )
        first = service.execute(preview, confirmation_token=preview.confirmation_token)
        second = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(first.status, SyncStatus.FAILED)
        self.assertEqual(second.status, SyncStatus.FAILED)
        self.assertIn("already used", second.message)
        self.assertEqual(len(jira.create_calls), 1)

    def test_successful_create_is_idempotently_suppressed(self) -> None:
        jira = FakeJira()
        metadata = FakeMetadata()
        repository = MemoryTicketRepository()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(_create(), dry_run=False)
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            repository=repository,
            preview_service=preview_service,
        )
        first = service.execute(preview, confirmation_token=preview.confirmation_token)
        second = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(first.status, SyncStatus.CREATED)
        self.assertEqual(second.status, SyncStatus.DUPLICATE_PREVENTED)
        self.assertEqual(second.issue_key, "DAH-101")
        self.assertEqual(len(jira.create_calls), 1)

        # A subsequent bad confirmation must not shadow the durable success and
        # make a later invocation eligible for a second POST.
        third = service.execute(preview, confirmation_token="wrong")
        fourth = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(third.status, SyncStatus.DUPLICATE_PREVENTED)
        self.assertEqual(fourth.status, SyncStatus.DUPLICATE_PREVENTED)
        self.assertEqual(len(jira.create_calls), 1)

    def test_update_uses_fresh_timestamp_and_blocks_conflict(self) -> None:
        jira = FakeJira(_snapshot())
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(_update(), dry_run=False)
        jira.snapshot = _snapshot(updated=NOW + timedelta(minutes=1))
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            preview_service=preview_service,
        )
        result = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(result.status, SyncStatus.CONFLICT)
        self.assertEqual(jira.update_calls, [])

    def test_adapter_detected_last_moment_concurrency_is_a_conflict(self) -> None:
        jira = FakeJira(_snapshot())
        jira.update_error = ConcurrencyConflict("changed during PUT guard")
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(_update(), dry_run=False)
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            preview_service=preview_service,
        )
        result = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(result.status, SyncStatus.CONFLICT)
        self.assertEqual(result.action, Action.UPDATE)
        self.assertEqual(len(jira.update_calls), 1)

    def test_update_passes_expected_updated_and_adds_only_new_related_items(self) -> None:
        jira = FakeJira(_snapshot())
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        command = _update(
            attachments=(Attachment("new.txt"),),
            links=(IssueLink("200", "DAH-8"),),
        )
        preview = preview_service.preview(command, dry_run=False)
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            preview_service=preview_service,
        )
        result = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(result.status, SyncStatus.UPDATED)
        self.assertEqual(jira.update_calls, [("DAH-1", {"summary": "Changed"}, NOW)])
        self.assertEqual(jira.attachment_calls, [("DAH-1", "new.txt")])
        self.assertEqual(jira.link_calls, [("DAH-1", "DAH-8")])

        repeated = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(repeated.status, SyncStatus.DUPLICATE_PREVENTED)
        self.assertEqual(jira.attachment_calls, [("DAH-1", "new.txt")])
        self.assertEqual(jira.link_calls, [("DAH-1", "DAH-8")])

    def test_unclassified_update_transport_failure_is_uncertain(self) -> None:
        jira = FakeJira(_snapshot())
        jira.update_error = ConnectionResetError("connection dropped after PUT")
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(_update(), dry_run=False)
        result = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            preview_service=preview_service,
        ).execute(preview, confirmation_token=preview.confirmation_token)

        self.assertEqual(result.status, SyncStatus.UNCERTAIN)
        self.assertEqual(result.action, Action.UPDATE)
        self.assertEqual(len(jira.update_calls), 1)

    def test_related_transport_failure_is_uncertain_but_definite_rejection_is_retryable(
        self,
    ) -> None:
        class RelatedJira(FakeJira):
            def upload_attachment(self, key, attachment):
                self.attachment_calls.append((key, attachment.reference))
                if attachment.reference == "unclear.txt":
                    raise TimeoutError("response lost")
                raise DefiniteWriteError("file rejected")

        jira = RelatedJira(_snapshot())
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(
            _update(
                attachments=(Attachment("unclear.txt"), Attachment("rejected.txt")),
            ),
            dry_run=False,
        )
        result = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            preview_service=preview_service,
        ).execute(preview, confirmation_token=preview.confirmation_token)

        self.assertEqual(result.status, SyncStatus.UPDATED)
        self.assertTrue(result.related[0].uncertain)
        self.assertFalse(result.related[1].uncertain)
        self.assertFalse(result.related[0].succeeded)
        self.assertFalse(result.related[1].succeeded)

    def test_related_work_produces_one_final_result_and_one_audit(self) -> None:
        events = []
        repository = RecordingRepository(events)
        audit = RecordingAudit()
        jira = FakeJira(events=events)
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(
            _create(attachments=(Attachment("one.txt"),)),
            dry_run=False,
        )
        SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            repository=repository,
            audit=audit,
            preview_service=preview_service,
        ).execute(preview, confirmation_token=preview.confirmation_token)

        self.assertEqual(
            [event for event in events if event[0] == "save_result"],
            [("save_result", SyncStatus.CREATED)],
        )
        self.assertEqual(len(audit.events), 1)

    def test_critical_field_context_change_blocks_write(self) -> None:
        metadata = FakeMetadata(available_fields=frozenset({"story_points"}))
        jira = FakeJira()
        command = TicketCommand(
            "r1",
            Action.CREATE,
            "DAH",
            IssueType.STORY,
            "Story",
            fields={"components": ["Data"], "story_points": 3},
        )
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(command, dry_run=False)
        metadata.force_available_fields = frozenset()
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            preview_service=preview_service,
        )
        result = service.execute(preview, confirmation_token=preview.confirmation_token)
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("availability changed", result.message)
        self.assertEqual(jira.create_calls, [])

    def test_execute_many_isolates_definite_failure_by_row(self) -> None:
        class SelectiveJira(FakeJira):
            def create_issue(self, payload):
                self.create_calls.append(dict(payload))
                if payload["summary"] == "bad":
                    raise DefiniteWriteError("Jira rejected synthetic row")
                return CreateResult("DAH-" + str(len(self.create_calls)))

        jira = SelectiveJira()
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        previews = preview_service.preview_many(
            (_create("one", "good"), _create("two", "bad"), _create("three", "last")),
            dry_run=False,
        )
        service = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(("DAH",)),
            preview_service=preview_service,
        )
        results = service.execute_many(
            previews,
            confirmation_tokens={
                preview.preview_id: preview.confirmation_token for preview in previews
            },
        )
        self.assertEqual(
            [result.status for result in results],
            [SyncStatus.CREATED, SyncStatus.FAILED, SyncStatus.CREATED],
        )
        self.assertEqual(len(jira.create_calls), 3)

    def test_ignore_never_loads_metadata_or_jira(self) -> None:
        jira = FakeJira()
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(
            TicketCommand("r1", Action.IGNORE, "", IssueType.INCIDENT),
            dry_run=False,
        )
        result = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(()),
            preview_service=preview_service,
        ).execute(preview)
        self.assertEqual(result.status, SyncStatus.IGNORED)
        self.assertEqual(metadata.calls, [])
        self.assertEqual(jira.fetch_calls, [])

    def test_invalid_ignore_is_failed_instead_of_silently_ignored(self) -> None:
        jira = FakeJira()
        metadata = FakeMetadata()
        preview_service = PreviewService(jira, metadata)
        preview = preview_service.preview(
            TicketCommand("r1", Action.IGNORE, "", IssueType.INCIDENT),
            dry_run=False,
        )
        invalid = replace(
            preview,
            messages=(
                ValidationMessage(
                    code="BROKEN_ROW",
                    message="Invalid ignored row",
                    severity=Severity.ERROR,
                ),
            ),
        )
        result = SyncService(
            jira,
            metadata,
            StaticProjectPolicy(()),
            preview_service=preview_service,
        ).execute(invalid)
        self.assertEqual(result.status, SyncStatus.FAILED)


def _create(
    row="r1",
    summary="Incident",
    *,
    attachments=(),
    links=(),
):
    return TicketCommand(
        row,
        Action.CREATE,
        "DAH",
        IssueType.INCIDENT,
        summary,
        attachments=attachments,
        links=links,
    )


def _update(*, attachments=(), links=()):
    return TicketCommand(
        "r1",
        Action.UPDATE,
        "DAH",
        IssueType.STORY,
        "Changed",
        jira_key="DAH-1",
        attachments=attachments,
        links=links,
    )


def _snapshot(updated=NOW):
    return TicketSnapshot(
        "DAH-1",
        "DAH",
        IssueType.STORY,
        "alice",
        "Open",
        updated,
        fields={"summary": "Old", "components": ["Data"]},
    )


if __name__ == "__main__":
    unittest.main()
