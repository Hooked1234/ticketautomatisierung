from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ticketpilot.infrastructure.persistence import CURRENT_SCHEMA_VERSION, SQLiteStore
from ticketpilot.infrastructure.security import REDACTED, SensitiveDataError


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(datetime(2026, 7, 21, 10, 0, tzinfo=UTC))
        self.store = SQLiteStore(":memory:", clock=self.clock)

    def tearDown(self) -> None:
        self.store.close()

    def test_schema_is_versioned_and_reversible(self) -> None:
        self.assertEqual(self.store.schema_version, CURRENT_SCHEMA_VERSION)

        self.store.migrate(1)
        self.assertEqual(self.store.schema_version, 1)
        self.store.migrate(0)
        self.assertEqual(self.store.schema_version, 0)
        self.store.migrate(CURRENT_SCHEMA_VERSION)
        self.assertEqual(self.store.schema_version, CURRENT_SCHEMA_VERSION)

    def test_settings_roundtrip_and_credentials_are_refused(self) -> None:
        self.store.set_settings(
            {
                "visible_columns": ["summary", "priority"],
                "setup_completed": False,
            }
        )

        self.assertEqual(
            self.store.get_setting("visible_columns"),
            ["summary", "priority"],
        )
        self.assertIs(self.store.get_setting("setup_completed"), False)
        self.assertEqual(self.store.get_setting("missing", "fallback"), "fallback")
        self.assertTrue(self.store.delete_setting("visible_columns"))
        self.assertFalse(self.store.delete_setting("visible_columns"))

        for key, value in (
            ("jira_token", "secret"),
            ("setup", {"Authorization": "Bearer secret"}),
        ):
            with self.subTest(key=key), self.assertRaises(SensitiveDataError):
                self.store.set_setting(key, value)

        with self.assertRaises(SensitiveDataError):
            self.store.set_settings({"safe_first": True, "api_token": "secret"})
        self.assertIsNone(self.store.get_setting("safe_first"))

    def test_drafts_and_tickets_roundtrip_without_data_loss(self) -> None:
        identifier = self.store.save_draft(
            {"summary": "Entwurf", "description": "Lange Beschreibung", "labels": ["one"]},
            draft_id="draft-1",
        )
        self.store.upsert_ticket(
            "row-1",
            {"summary": "Entwurf", "story_points": 3.5},
            jira_key="dah-123",
            action="UPDATE",
            status="READY",
        )

        self.assertEqual(identifier, "draft-1")
        self.assertEqual(self.store.get_draft("draft-1")["data"]["summary"], "Entwurf")
        ticket = self.store.get_ticket("DAH-123")
        self.assertEqual(ticket["local_id"], "row-1")
        self.assertEqual(ticket["jira_key"], "DAH-123")
        self.assertEqual(ticket["data"]["story_points"], 3.5)
        self.assertEqual(len(self.store.list_drafts()), 1)
        self.assertEqual(len(self.store.list_tickets()), 1)

        self.assertTrue(self.store.delete_draft("draft-1"))
        self.assertIsNone(self.store.get_draft("draft-1"))

    def test_audit_and_sync_results_are_sanitized_and_isolated(self) -> None:
        audit_id = self.store.append_audit(
            "SYNC_PREVIEW",
            entity_type="ticket-row",
            entity_id="row-1",
            details={"authorization": "Bearer never-store", "result": "valid"},
        )
        first = self.store.add_sync_result(
            action="CREATE",
            outcome="DRY_RUN",
            message="Authorization: Bearer another-secret",
            local_id="row-1",
            details={"token": "third-secret", "valid": True},
        )
        second = self.store.add_sync_result(
            action="CREATE",
            outcome="FAILED",
            local_id="row-2",
        )

        self.assertGreater(audit_id, 0)
        self.assertGreater(second, first)
        audit = self.store.list_audit()[0]
        self.assertEqual(audit["details"]["authorization"], REDACTED)
        result = self.store.list_sync_results(local_id="row-1")[0]
        self.assertNotIn("another-secret", result["message"])
        self.assertEqual(result["details"]["token"], REDACTED)
        self.assertEqual(len(self.store.list_sync_results(local_id="row-2")), 1)

    def test_metadata_cache_is_fresh_for_less_than_24_hours_and_stale_at_expiry(self) -> None:
        entry = self.store.put_metadata(
            "metadata:DAH:priorities",
            "priorities",
            [{"id": "demo:high", "label": "High"}],
            project_key="dah",
        )

        self.assertTrue(entry.is_fresh)
        self.clock.value += timedelta(hours=23, minutes=59)
        self.assertIsNotNone(self.store.get_metadata("metadata:DAH:priorities"))
        status = self.store.cache_status()
        self.assertEqual((status.total, status.fresh, status.stale), (1, 1, 0))

        self.clock.value += timedelta(minutes=1)
        self.assertIsNone(self.store.get_metadata("metadata:DAH:priorities"))
        stale = self.store.get_metadata_entry(
            "metadata:DAH:priorities", allow_stale=True
        )
        self.assertFalse(stale.is_fresh)
        self.assertEqual(stale.value[0]["label"], "High")
        self.assertEqual(self.store.cache_status().stale, 1)
        self.assertEqual(self.store.purge_expired_metadata(), 1)

    def test_changed_cache_ttl_applies_to_subsequent_refreshes(self) -> None:
        self.store.set_cache_ttl(timedelta(hours=1))
        entry = self.store.put_metadata(
            "metadata:DAH:teams",
            "teams",
            [{"id": "demo:analytics", "label": "Analytics"}],
            project_key="DAH",
        )

        self.assertEqual(entry.expires_at - entry.fetched_at, timedelta(hours=1))
        self.clock.value += timedelta(hours=1)
        self.assertIsNone(self.store.get_metadata("metadata:DAH:teams"))

        with self.assertRaises(ValueError):
            self.store.set_cache_ttl(timedelta(0))

    def test_changed_cache_ttl_rebases_existing_entries_from_fetch_time(self) -> None:
        original = self.store.put_metadata(
            "metadata:DAH:sprints",
            "sprints",
            [{"id": "demo:sprint:active", "label": "Demo Sprint aktuell"}],
            project_key="DAH",
        )
        self.clock.value += timedelta(hours=2)

        self.store.set_cache_ttl(timedelta(hours=1))

        rebased = self.store.get_metadata_entry(
            "metadata:DAH:sprints", allow_stale=True
        )
        self.assertIsNotNone(rebased)
        assert rebased is not None
        self.assertEqual(rebased.expires_at, original.fetched_at + timedelta(hours=1))
        self.assertFalse(rebased.is_fresh)
        self.assertIsNone(self.store.get_metadata("metadata:DAH:sprints"))
        self.assertEqual(
            (self.store.cache_status().fresh, self.store.cache_status().stale),
            (0, 1),
        )

    def test_comments_upsert_and_since_filter(self) -> None:
        created = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
        self.store.upsert_comment(
            jira_key="dah-1",
            comment_id="10",
            author="Alex",
            body="Erste Version",
            created_at=created,
        )
        self.clock.value += timedelta(hours=1)
        self.store.upsert_comment(
            jira_key="DAH-1",
            comment_id="10",
            author="Alex",
            body="Bearbeitete Version",
            created_at=created,
            updated_at=created + timedelta(minutes=20),
            data={"visibility": "team"},
        )

        comments = self.store.list_comments("DAH-1")
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["body"], "Bearbeitete Version")
        self.assertGreater(comments[0]["last_seen_at"], comments[0]["first_seen_at"])
        self.assertEqual(self.store.list_comments("DAH-1", since=created), [])

    def test_database_file_never_contains_rejected_or_redacted_secret(self) -> None:
        self.store.close()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ticketpilot.sqlite3"
            store = SQLiteStore(path, clock=self.clock)
            with self.assertRaises(SensitiveDataError):
                store.save_draft({"token": "do-not-write"}, draft_id="unsafe")
            store.append_audit("ERROR", details={"Authorization": "Bearer do-not-write"})
            store.close()

            self.assertNotIn(b"do-not-write", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
