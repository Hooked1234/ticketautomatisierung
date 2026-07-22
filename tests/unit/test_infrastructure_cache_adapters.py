from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from ticketpilot.application import PreviewService, StaticProjectPolicy, SyncService
from ticketpilot.application.ports import (
    AuditEvent,
    AuditSink,
    MetadataContext,
    TicketRepository,
)
from ticketpilot.domain.models import (
    Action,
    IssueType,
    RelatedOperationResult,
    RowResult,
    SyncStatus,
    TicketCommand,
)
from ticketpilot.infrastructure.adapters import SQLiteAuditSink, SQLiteTicketRepository
from ticketpilot.infrastructure.gateways import LocalDemoGateway
from ticketpilot.infrastructure.metadata_cache import CachingMetadataProvider
from ticketpilot.infrastructure.persistence import SQLiteStore


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class CountingMetadataGateway(LocalDemoGateway):
    def __init__(self, clock: MutableClock) -> None:
        super().__init__(clock=clock)
        self.metadata_calls = 0
        self.context_calls = 0
        self.fail = False

    def get_metadata(
        self,
        kind: str,
        *,
        project: str = "DAH",
        force_refresh: bool = False,
    ) -> object:
        self.metadata_calls += 1
        if self.fail:
            raise RuntimeError("Authorization: Bearer never-leak")
        return super().get_metadata(kind, project=project, force_refresh=force_refresh)

    def get_context(
        self,
        project: str,
        issue_type: IssueType,
        *,
        force_refresh: bool = False,
    ) -> MetadataContext:
        self.context_calls += 1
        if self.fail:
            raise RuntimeError("temporary offline")
        return super().get_context(project, issue_type, force_refresh=force_refresh)


class MetadataCacheAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(datetime(2026, 7, 21, 10, 0, tzinfo=UTC))
        self.store = SQLiteStore(":memory:", clock=self.clock)
        self.upstream = CountingMetadataGateway(self.clock)
        self.provider = CachingMetadataProvider(self.upstream, self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_small_metadata_and_context_are_cached_for_24_hours(self) -> None:
        first = self.provider.get_metadata("priorities")
        second = self.provider.get_metadata("priorities")
        context_first = self.provider.get_context("DAH", IssueType.STORY)
        context_second = self.provider.get_context("DAH", IssueType.STORY)

        self.assertEqual(first, second)
        self.assertEqual(context_first, context_second)
        self.assertEqual(self.upstream.metadata_calls, 1)
        self.assertEqual(self.upstream.context_calls, 1)

        self.clock.value += timedelta(hours=24)
        self.provider.get_metadata("priorities")
        self.assertEqual(self.upstream.metadata_calls, 2)

    def test_offline_falls_back_to_stale_but_forced_critical_refresh_does_not(self) -> None:
        cached = self.provider.get_context("DAH", IssueType.BUG)
        self.clock.value += timedelta(hours=25)
        self.upstream.fail = True

        fallback = self.provider.get_context("DAH", IssueType.BUG)
        self.assertEqual(fallback.revision, cached.revision)
        with self.assertRaises(RuntimeError):
            self.provider.get_context("DAH", IssueType.BUG, force_refresh=True)

    def test_large_metadata_kinds_are_rejected_for_on_demand_search(self) -> None:
        for kind in ("people", "epics", "issues", "products_services", "accounts"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.provider.get_metadata(kind)

    def test_manual_refresh_is_row_isolated_and_sanitizes_errors(self) -> None:
        results = self.provider.refresh(kinds=("priorities", "not_supported"))
        self.assertTrue(results[0].succeeded)
        self.assertFalse(results[1].succeeded)

        self.upstream.fail = True
        result = self.provider.refresh(kinds=("priorities",))[0]
        self.assertFalse(result.succeeded)
        self.assertNotIn("never-leak", result.message)


class ApplicationPortAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(datetime(2026, 7, 21, 10, 0, tzinfo=UTC))
        self.store = SQLiteStore(":memory:", clock=self.clock)

    def tearDown(self) -> None:
        self.store.close()

    def test_ticket_repository_roundtrips_full_row_result_and_jira_key(self) -> None:
        repository = SQLiteTicketRepository(self.store)
        self.assertIsInstance(repository, TicketRepository)
        result = RowResult(
            row_id="row-1",
            status=SyncStatus.CREATED,
            timestamp=self.clock.value,
            issue_key="DAH-123",
            message="Erstellt",
            request_fingerprint="fingerprint-1",
            related=(
                RelatedOperationResult(
                    kind="attachment",
                    reference="demo.pdf",
                    succeeded=False,
                    message="Nur synthetischer Test",
                    uncertain=True,
                ),
            ),
        )

        repository.save_jira_key("row-1", "DAH-123")
        repository.save_result(result)
        restored = repository.get_last_result("row-1")

        self.assertEqual(restored, result)
        self.assertEqual(self.store.get_ticket("row-1")["jira_key"], "DAH-123")

    def test_audit_sink_implements_port_and_redacts_detail(self) -> None:
        sink = SQLiteAuditSink(self.store)
        self.assertIsInstance(sink, AuditSink)
        sink.record(
            AuditEvent(
                occurred_at=self.clock.value,
                event_type="PREVIEW",
                row_id="row-1",
                outcome="VALID",
                issue_key=None,
                detail="Authorization: Bearer no-persistence",
                dry_run=False,
            )
        )

        audit = self.store.list_audit()[0]
        self.assertEqual(audit["entity_id"], "row-1")
        self.assertNotIn("no-persistence", audit["details"]["detail"])
        self.assertIs(audit["details"]["dry_run"], False)

    def test_failed_and_uncertain_update_keep_explicit_or_persisted_action(self) -> None:
        repository = SQLiteTicketRepository(self.store)
        self.store.upsert_ticket(
            "update-row",
            {"summary": "Update"},
            jira_key="DAH-1",
            action="UPDATE",
            status="DRAFT",
        )
        failed = RowResult(
            row_id="update-row",
            status=SyncStatus.FAILED,
            timestamp=self.clock.value,
            issue_key="DAH-1",
            action=Action.UPDATE,
        )
        repository.save_result(failed)
        uncertain = RowResult(
            row_id="update-row",
            status=SyncStatus.UNCERTAIN,
            timestamp=self.clock.value,
            issue_key="DAH-1",
        )
        repository.save_result(uncertain)
        rows = self.store.list_sync_results(local_id="update-row")
        self.assertEqual([row["action"] for row in rows], ["UPDATE", "UPDATE"])
        self.assertEqual(repository.get_last_result("update-row").action, None)

    def test_create_reservation_is_atomic_and_survives_new_service_and_connection(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "reservation.sqlite3"
            first_store = SQLiteStore(path, clock=self.clock)
            second_store = SQLiteStore(path, clock=self.clock)
            try:
                first_repository = SQLiteTicketRepository(first_store)
                second_repository = SQLiteTicketRepository(second_store)
                reservation = RowResult(
                    row_id="reserved-row",
                    status=SyncStatus.UNCERTAIN,
                    timestamp=self.clock.value,
                    message="reserved before POST",
                    request_fingerprint="fingerprint",
                    action=Action.CREATE,
                )
                self.assertTrue(first_repository.reserve_create(reservation))
                self.assertFalse(second_repository.reserve_create(reservation))
                self.assertEqual(
                    second_repository.get_last_result("reserved-row").status,
                    SyncStatus.UNCERTAIN,
                )

                class CountingGateway(LocalDemoGateway):
                    def __init__(self) -> None:
                        super().__init__()
                        self.create_calls = 0

                    def create_issue(self, payload):
                        self.create_calls += 1
                        return super().create_issue(payload)

                gateway = CountingGateway()
                preview_service = PreviewService(gateway, gateway)
                preview = preview_service.preview(
                    TicketCommand(
                        "reserved-row",
                        Action.CREATE,
                        "DAH",
                        IssueType.INCIDENT,
                        "Must not be posted",
                    ),
                    dry_run=False,
                )
                result = SyncService(
                    gateway,
                    gateway,
                    StaticProjectPolicy(("DAH",)),
                    repository=second_repository,
                    preview_service=preview_service,
                ).execute(preview, confirmation_token=preview.confirmation_token)
                self.assertEqual(result.status, SyncStatus.UNCERTAIN)
                self.assertEqual(gateway.create_calls, 0)
            finally:
                second_store.close()
                first_store.close()


if __name__ == "__main__":
    unittest.main()
