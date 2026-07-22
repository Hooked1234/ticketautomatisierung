from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ticketpilot.application import DefiniteWriteError
from ticketpilot.domain import AssigneeMode, AssigneeSelection
from ticketpilot.facade import OfflineTicketPilotFacade
from ticketpilot.infrastructure import MemoryCredentialStore, SQLiteStore
from ticketpilot.ui.contracts import (
    AttachmentDraft,
    CommentFilter,
    ConnectionResult,
    DashboardFilter,
    ExecutionRequest,
    LinkDraft,
    TicketDraft,
)


class _PersistentMemoryCredentialStore(MemoryCredentialStore):
    """Deterministic stand-in for Windows Credential Manager in tests."""

    persistent = True

    def __init__(self, fail_operation: str = "") -> None:
        super().__init__()
        self.fail_operation = fail_operation
        self.mutations: list[str] = []

    def save(self, service: str, username: str, secret: str) -> None:
        self.mutations.append("save")
        if self.fail_operation == "save":
            raise RuntimeError("credential backend save failed")
        super().save(service, username, secret)

    def delete(self, service: str, username: str) -> None:
        self.mutations.append("delete")
        if self.fail_operation == "delete":
            raise RuntimeError("credential backend delete failed")
        super().delete(service, username)


@pytest.fixture()
def facade(tmp_path: Path) -> OfflineTicketPilotFacade:
    instance = OfflineTicketPilotFacade(
        SQLiteStore(tmp_path / "ticketpilot.sqlite3"),
        data_directory=tmp_path,
        credentials=MemoryCredentialStore(),
    )
    yield instance
    instance.close()


def test_offline_start_refresh_and_full_read_views(
    facade: OfflineTicketPilotFacade,
    tmp_path: Path,
) -> None:
    initial = facade.startup_snapshot()
    assert initial.dry_run is True
    assert initial.online is False
    assert initial.project_key == "DAH"

    refreshed = facade.refresh_metadata()
    assert refreshed.cache_updated_at is not None
    assert facade.editor_options("Story", "DAH")["components"]
    assert facade.search("people", "Alex", {"project": "DAH"})[0].value == "alex.example"
    assert facade.search("accounts", "None", {"project": "DAH"})[0].value == ""

    report = facade.load_dashboard(DashboardFilter())
    assert report.result_count == 4
    assert {item.key for item in report.metrics} >= {"all", "overdue", "impediments"}
    assert facade.list_comments(CommentFilter())

    destination = facade.export_dashboard_csv(DashboardFilter(), tmp_path / "report.csv")
    assert destination.read_text(encoding="utf-8").startswith("\ufeffJira Key")


def test_selected_board_filters_sprints_and_dashboard_uses_labels(
    facade: OfflineTicketPilotFacade,
) -> None:
    facade.refresh_metadata()
    settings = facade.load_settings()
    assert {item.value for item in settings.boards} == {"demo:board:1", "demo:board:2"}

    facade.save_settings(replace(settings, selected_board="demo:board:2"))
    expected_service = {"demo:sprint:service"}
    assert {
        item.value for item in facade.editor_options("Story", "DAH")["sprints"]
    } == expected_service
    assert {
        item.value for item in facade.dashboard_options()["sprint"]
    } == expected_service
    assert {item.value for item in facade.search("sprints", "", {"project": "DAH"})} == (
        expected_service
    )

    facade.save_settings(replace(facade.load_settings(), selected_board="demo:board:1"))
    assert {
        item.value for item in facade.editor_options("Story", "DAH")["sprints"]
    } == {"demo:sprint:active", "demo:sprint:future"}

    report = facade.load_dashboard(DashboardFilter(sprint="demo:sprint:active"))
    assert report.result_count == 2
    assert tuple(item.label for item in report.breakdowns["sprint"]) == (
        "Demo Sprint aktuell",
    )


def test_create_dry_run_is_durable_and_does_not_change_demo_jira(
    facade: OfflineTicketPilotFacade,
) -> None:
    before = facade.load_dashboard(DashboardFilter()).result_count
    draft = TicketDraft(
        project="DAH",
        issue_type="Story",
        summary="Vollständig lokaler Test",
        values={"components": ["BI"], "description": "Kein Netzwerk"},
    )
    preview = facade.preview((draft,))[0]
    assert preview.valid is True
    assert preview.dry_run is True

    result = facade.execute(ExecutionRequest((preview.preview_id,), confirmed=False, dry_run=True))[
        0
    ]
    assert result.status == "Dry Run erfolgreich"
    assert result.jira_key == ""
    assert facade.load_dashboard(DashboardFilter()).result_count == before
    assert facade.list_results()[0].row_id == draft.local_id


def test_confirmed_demo_create_runs_once_and_never_uses_network(
    facade: OfflineTicketPilotFacade,
) -> None:
    settings = replace(facade.load_settings(), dry_run=False)
    facade.save_settings(settings)
    before = facade.load_dashboard(DashboardFilter()).result_count
    draft = TicketDraft(
        project="DAH",
        issue_type="Bug",
        summary="Synthetischer bestätigter Test",
        values={"components": ["Data Platform"]},
    )
    preview = facade.preview((draft,))[0]
    assert preview.valid and not preview.dry_run

    request = ExecutionRequest((preview.preview_id,), confirmed=True, dry_run=False)
    first = facade.execute(request)[0]
    second = facade.execute(request)[0]
    assert first.status == "Erstellt"
    assert first.jira_key.startswith("DAH-")
    assert second == first
    assert facade.load_dashboard(DashboardFilter()).result_count == before + 1


def test_update_preview_uses_clear_marker_and_remote_identity(
    facade: OfflineTicketPilotFacade,
) -> None:
    draft = TicketDraft(
        action="UPDATE",
        project="DAH",
        issue_type="Epic",  # facade safely resolves the immutable remote type
        summary="",
        jira_key="DAH-90001",
        values={"description": "<CLEAR>", "components": ""},
    )
    preview = facade.preview((draft,))[0]
    assert preview.valid
    assert preview.issue_type == "Story"
    assert any(item.field == "description" and item.change == "clear" for item in preview.diffs)


def test_loaded_snapshot_update_filters_reporting_fields_and_empty_multiselects(
    facade: OfflineTicketPilotFacade,
) -> None:
    loaded = facade.load_ticket("DAH-90001")
    assert loaded.action == "UPDATE"
    assert "created" not in loaded.values
    assert "status_category" not in loaded.values

    loaded.values["components"] = []  # empty UPDATE control means unchanged
    loaded.values["sprint"] = "<CLEAR>"
    preview = facade.preview((loaded,))[0]
    assert preview.valid, preview.validation
    assert not any(item.field == "components" for item in preview.diffs)
    assert any(item.field == "sprint" and item.change == "clear" for item in preview.diffs)


def test_setup_never_persists_token_in_sqlite_settings(tmp_path: Path) -> None:
    credentials = _PersistentMemoryCredentialStore()
    store = SQLiteStore(tmp_path / "app.sqlite3")
    facade = OfflineTicketPilotFacade(store, data_directory=tmp_path, credentials=credentials)
    try:
        setup = replace(
            facade.load_setup(),
            username="personal.user",
            configured=True,
            remember_token=True,
        )
        result = facade.save_setup(setup, "very-secret-personal-token")
        assert result.ok
        assert credentials.load("jira", "personal.user") == "very-secret-personal-token"
        assert facade.load_setup().credential_storage_available is True
        assert facade.load_setup().remember_token is True
        serialized_settings = repr(store.list_settings())
        assert "very-secret-personal-token" not in serialized_settings
        assert "token" not in {key.casefold() for key in store.list_settings()}
    finally:
        facade.close()


def test_memory_credential_fallback_is_session_only_and_disclosed(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    store = SQLiteStore(tmp_path / "app.sqlite3")
    facade = OfflineTicketPilotFacade(store, data_directory=tmp_path, credentials=credentials)
    try:
        setup = replace(
            facade.load_setup(),
            username="personal.user",
            configured=True,
            remember_token=True,
        )
        result = facade.save_setup(setup, "session-only-secret")

        assert result.ok
        assert credentials.load("jira", "demo.user") is None
        assert facade._session_token == "session-only-secret"
        loaded = facade.load_setup()
        assert loaded.credential_storage_available is False
        assert loaded.remember_token is False
        assert store.get_setting("remember_signin") is False
    finally:
        facade.close()


@pytest.mark.parametrize("operation, remember", [("save", True), ("delete", False)])
def test_credential_failure_does_not_partially_commit_setup(
    tmp_path: Path,
    operation: str,
    remember: bool,
) -> None:
    credentials = _PersistentMemoryCredentialStore(fail_operation=operation)
    store = SQLiteStore(tmp_path / "app.sqlite3")
    facade = OfflineTicketPilotFacade(store, data_directory=tmp_path, credentials=credentials)
    try:
        original_settings = store.list_settings()
        original_url = facade.startup_snapshot().jira_url
        result = facade.save_setup(
            replace(
                facade.load_setup(),
                jira_url="https://new-jira.example.invalid",
                remember_token=remember,
            ),
            "never-committed-secret",
        )

        assert result.ok is False
        assert store.list_settings() == original_settings
        assert facade.startup_snapshot().jira_url == original_url
        assert facade.load_setup().configured is False
        assert facade._session_token is None
    finally:
        facade.close()


def test_failed_connection_test_does_not_touch_credentials_or_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = _PersistentMemoryCredentialStore()
    store = SQLiteStore(tmp_path / "app.sqlite3")
    facade = OfflineTicketPilotFacade(store, data_directory=tmp_path, credentials=credentials)
    try:
        monkeypatch.setattr(
            facade,
            "test_connection",
            lambda _url, _token: ConnectionResult(False, "Nicht erreichbar", "read-only"),
        )
        original_settings = store.list_settings()
        result = facade.save_setup(
            replace(facade.load_setup(), jira_url="https://unreachable.example.invalid"),
            "connection-secret",
        )

        assert result.ok is False
        assert credentials.mutations == []
        assert store.list_settings() == original_settings
        assert facade.load_setup().configured is False
    finally:
        facade.close()


def test_save_setup_and_recomposition_preserve_demo_gateway_state_and_sequence(
    facade: OfflineTicketPilotFacade,
) -> None:
    facade.save_settings(replace(facade.load_settings(), dry_run=False))
    first_draft = TicketDraft(
        project="DAH",
        issue_type="Incident",
        summary="Gateway state one",
    )
    first_preview = facade.preview((first_draft,))[0]
    first = facade.execute(
        ExecutionRequest((first_preview.preview_id,), confirmed=True, dry_run=False)
    )[0]
    gateway = facade._gateway
    count_after_first = facade.load_dashboard(DashboardFilter()).result_count

    result = facade.save_setup(replace(facade.load_setup(), configured=True), "")
    assert result.ok
    assert facade._gateway is gateway
    assert facade.load_dashboard(DashboardFilter()).result_count == count_after_first

    second_draft = TicketDraft(
        project="DAH",
        issue_type="Incident",
        summary="Gateway state two",
    )
    second_preview = facade.preview((second_draft,))[0]
    second = facade.execute(
        ExecutionRequest((second_preview.preview_id,), confirmed=True, dry_run=False)
    )[0]
    assert int(second.jira_key.rsplit("-", 1)[1]) == int(first.jira_key.rsplit("-", 1)[1]) + 1


def test_safety_setting_changes_expire_old_live_previews_and_gate_current_dry_run(
    facade: OfflineTicketPilotFacade,
) -> None:
    facade.save_settings(replace(facade.load_settings(), dry_run=False))
    draft = TicketDraft(project="DAH", issue_type="Incident", summary="Old live preview")
    preview = facade.preview((draft,))[0]
    facade.save_settings(replace(facade.load_settings(), dry_run=True))
    stale_dry = facade.execute(
        ExecutionRequest((preview.preview_id,), confirmed=True, dry_run=False)
    )[0]
    assert stale_dry.status == "Fehler"

    project_preview = facade.preview(
        (TicketDraft(project="DAH", issue_type="Incident", summary="Old project preview"),)
    )[0]
    facade.save_settings(
        replace(
            facade.load_settings(),
            allowed_projects=("DAH", "OPS"),
            selected_project="OPS",
        )
    )
    stale_project = facade.execute(
        ExecutionRequest((project_preview.preview_id,), confirmed=False, dry_run=True)
    )[0]
    assert stale_project.status == "Fehler"
    assert "abgelaufen" in stale_project.message

    allowlist_preview = facade.preview(
        (TicketDraft(project="OPS", issue_type="Incident", summary="Old allowlist preview"),)
    )[0]
    facade.save_settings(
        replace(
            facade.load_settings(),
            allowed_projects=("DAH", "OPS", "LAB"),
        )
    )
    stale_allowlist = facade.execute(
        ExecutionRequest((allowlist_preview.preview_id,), confirmed=False, dry_run=True)
    )[0]
    assert stale_allowlist.status == "Fehler"
    assert "abgelaufen" in stale_allowlist.message


def test_audit_items_keep_historical_dry_run_mode_after_settings_change(
    facade: OfflineTicketPilotFacade,
) -> None:
    dry_draft = TicketDraft(project="DAH", issue_type="Incident", summary="Audit dry")
    dry_preview = facade.preview((dry_draft,))[0]
    facade.execute(ExecutionRequest((dry_preview.preview_id,), confirmed=False, dry_run=True))

    facade.save_settings(replace(facade.load_settings(), dry_run=False))
    live_draft = TicketDraft(project="DAH", issue_type="Incident", summary="Audit live")
    live_preview = facade.preview((live_draft,))[0]
    facade.execute(ExecutionRequest((live_preview.preview_id,), confirmed=True, dry_run=False))

    sync_audit = {item.target: item for item in facade.list_audit() if item.operation == "CREATE"}
    assert sync_audit[dry_draft.local_id].outcome == "DRY_RUN"
    assert sync_audit[dry_draft.local_id].dry_run is True
    assert sync_audit[live_draft.local_id].outcome == "CREATED"
    assert sync_audit[live_draft.local_id].dry_run is False


def test_successful_related_operations_are_not_persisted_as_pending_for_update(
    facade: OfflineTicketPilotFacade,
    tmp_path: Path,
) -> None:
    facade.save_settings(replace(facade.load_settings(), dry_run=False))
    good = tmp_path / "good.txt"
    retry = tmp_path / "retry.txt"
    good.write_text("synthetic", encoding="utf-8")
    retry.write_text("synthetic", encoding="utf-8")
    calls: list[str] = []
    original_upload = facade._gateway.upload_attachment

    def selective_upload(key: str, attachment: object) -> None:
        reference = str(attachment.reference)
        calls.append(reference)
        if reference == str(retry) and calls.count(reference) == 1:
            raise DefiniteWriteError("synthetic retryable attachment error")
        original_upload(key, attachment)

    facade._gateway.upload_attachment = selective_upload
    draft = TicketDraft(
        project="DAH",
        issue_type="Incident",
        summary="Pending related persistence",
        attachments=[AttachmentDraft(good), AttachmentDraft(retry)],
        links=[LinkDraft("demo:relates", "DAH-90001")],
    )
    preview = facade.preview((draft,))[0]
    first = facade.execute(ExecutionRequest((preview.preview_id,), confirmed=True, dry_run=False))[
        0
    ]
    assert first.status == "Erstellt"

    pending = facade.load_ticket(draft.local_id)
    assert pending.action == "UPDATE"
    assert pending.jira_key == first.jira_key
    assert [item.path for item in pending.attachments] == [retry]
    assert pending.links == []

    retry_preview = facade.preview((pending,))[0]
    second = facade.execute(
        ExecutionRequest((retry_preview.preview_id,), confirmed=True, dry_run=False)
    )[0]
    assert second.status == "Aktualisiert"
    assert calls == [str(good), str(retry), str(retry)]
    completed = facade.load_ticket(draft.local_id)
    assert completed.attachments == []
    assert completed.links == []


def test_facade_canonicalizes_assignee_choices_for_create_and_update(
    facade: OfflineTicketPilotFacade,
) -> None:
    create_unassigned = facade._draft_to_command(
        TicketDraft(project="DAH", issue_type="Incident", summary="A", values={"assignee": ""})
    )
    create_self = facade._draft_to_command(
        TicketDraft(project="DAH", issue_type="Incident", summary="B", values={"assignee": "@me"})
    )
    create_user = facade._draft_to_command(
        TicketDraft(
            project="DAH",
            issue_type="Incident",
            summary="C",
            values={"assignee": "alex.example"},
        )
    )
    assert create_unassigned.fields["assignee"] == AssigneeSelection(AssigneeMode.UNASSIGNED)
    assert create_self.fields["assignee"] == AssigneeSelection(AssigneeMode.SELF)
    assert create_user.fields["assignee"] == AssigneeSelection(
        AssigneeMode.USER,
        user_id="alex.example",
    )

    update_unchanged = facade._draft_to_command(
        TicketDraft(
            action="UPDATE",
            project="DAH",
            issue_type="Story",
            summary="",
            jira_key="DAH-90001",
            values={"assignee": ""},
        )
    )
    update_clear = facade._draft_to_command(
        TicketDraft(
            action="UPDATE",
            project="DAH",
            issue_type="Story",
            summary="",
            jira_key="DAH-90001",
            values={"assignee": "<CLEAR>"},
        )
    )
    assert update_unchanged.fields["assignee"] == ""
    assert update_clear.fields["assignee"] == "<CLEAR>"


def test_transport_uncertain_related_item_is_not_automatically_retried(
    facade: OfflineTicketPilotFacade,
    tmp_path: Path,
) -> None:
    facade.save_settings(replace(facade.load_settings(), dry_run=False))
    attachment_path = tmp_path / "applied-but-response-lost.txt"
    attachment_path.write_text("synthetic", encoding="utf-8")
    calls = 0
    original_upload = facade._gateway.upload_attachment

    def upload_then_timeout(key: str, attachment: object) -> None:
        nonlocal calls
        calls += 1
        original_upload(key, attachment)
        raise TimeoutError("response lost after apply")

    facade._gateway.upload_attachment = upload_then_timeout
    draft = TicketDraft(
        project="DAH",
        issue_type="Incident",
        summary="Uncertain related operation",
        attachments=[AttachmentDraft(attachment_path)],
    )
    preview = facade.preview((draft,))[0]
    result = facade.execute(
        ExecutionRequest((preview.preview_id,), confirmed=True, dry_run=False)
    )[0]

    assert result.status == "Erstellt"
    assert result.related[0].outcome == "UNCERTAIN"
    assert result.retry_allowed is False
    listed = next(item for item in facade.list_results() if item.row_id == draft.local_id)
    assert listed.related[0].reference == attachment_path.name
    assert listed.related[0].outcome == "UNCERTAIN"
    assert listed.uncertain is True
    pending = facade.load_ticket(draft.local_id)
    assert pending.attachments == []
    assert facade.preview((pending,))[0].attachment_names == ()
    assert calls == 1


def test_demo_created_ticket_survives_restart_and_sequence_remains_unique(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.sqlite3"
    first = OfflineTicketPilotFacade(
        SQLiteStore(path),
        data_directory=tmp_path,
        credentials=MemoryCredentialStore(),
    )
    first.save_settings(replace(first.load_settings(), dry_run=False))
    baseline = first.load_dashboard(DashboardFilter()).result_count
    draft = TicketDraft(project="DAH", issue_type="Incident", summary="Restart durable")
    preview = first.preview((draft,))[0]
    created = first.execute(
        ExecutionRequest((preview.preview_id,), confirmed=True, dry_run=False)
    )[0]
    local_id = draft.local_id
    first_key = created.jira_key
    assert first.load_dashboard(DashboardFilter()).result_count == baseline + 1
    first.close()

    reopened = OfflineTicketPilotFacade(
        SQLiteStore(path),
        data_directory=tmp_path,
        credentials=MemoryCredentialStore(),
    )
    try:
        assert reopened.load_dashboard(DashboardFilter()).result_count == baseline + 1
        loaded = reopened.load_ticket(local_id)
        assert loaded.action == "UPDATE"
        assert loaded.jira_key == first_key
        loaded.summary = "Restart durable updated"
        update_preview = reopened.preview((loaded,))[0]
        updated = reopened.execute(
            ExecutionRequest((update_preview.preview_id,), confirmed=True, dry_run=False)
        )[0]
        assert updated.status == "Aktualisiert"

        next_draft = TicketDraft(
            project="DAH",
            issue_type="Incident",
            summary="Restart unique sequence",
        )
        next_preview = reopened.preview((next_draft,))[0]
        next_created = reopened.execute(
            ExecutionRequest((next_preview.preview_id,), confirmed=True, dry_run=False)
        )[0]
        assert int(next_created.jira_key.rsplit("-", 1)[1]) > int(
            first_key.rsplit("-", 1)[1]
        )
    finally:
        reopened.close()


def test_execute_isolates_missing_preview_and_live_persistence_failure_per_row(
    facade: OfflineTicketPilotFacade,
) -> None:
    facade.save_settings(replace(facade.load_settings(), dry_run=False))
    first_draft = TicketDraft(project="DAH", issue_type="Incident", summary="Persist fail")
    second_draft = TicketDraft(project="DAH", issue_type="Incident", summary="Still runs")
    first_preview, second_preview = facade.preview((first_draft, second_draft))
    create_calls = 0
    original_create = facade._gateway.create_issue
    original_save = facade._store.save_draft

    def counted_create(payload: object) -> object:
        nonlocal create_calls
        create_calls += 1
        return original_create(payload)

    def selective_save(data: object, *, draft_id: str | None = None) -> str:
        if draft_id == first_draft.local_id:
            raise OSError("synthetic disk failure")
        return original_save(data, draft_id=draft_id)

    facade._gateway.create_issue = counted_create
    facade._store.save_draft = selective_save
    results = facade.execute(
        ExecutionRequest(
            ("missing-preview", first_preview.preview_id, second_preview.preview_id),
            confirmed=True,
            dry_run=False,
        )
    )

    assert [item.status for item in results] == ["Fehler", "Unklarer Zustand", "Erstellt"]
    assert results[1].retry_allowed is False
    assert results[1].uncertain is True
    assert create_calls == 2
    repeated = facade.execute(
        ExecutionRequest((first_preview.preview_id,), confirmed=True, dry_run=False)
    )[0]
    assert repeated == results[1]
    assert create_calls == 2


def test_bound_local_identity_cannot_be_reassigned_or_changed_to_create(
    facade: OfflineTicketPilotFacade,
) -> None:
    bound = TicketDraft(
        local_id="bound-row",
        action="UPDATE",
        project="DAH",
        issue_type="Story",
        summary="Bound",
        jira_key="DAH-90001",
    )
    facade.save_draft(bound)

    wrong_key = TicketDraft(
        local_id="bound-row",
        action="UPDATE",
        project="DAH",
        issue_type="Story",
        summary="Wrong key",
        jira_key="DAH-90002",
    )
    wrong_action = TicketDraft(
        local_id="bound-row",
        action="CREATE",
        project="DAH",
        issue_type="Story",
        summary="Wrong action",
    )
    previews = facade.preview((wrong_key, wrong_action))
    assert [item.valid for item in previews] == [False, False]
    assert all(facade._previews[item.preview_id].payload == {} for item in previews)


def test_search_widget_identifiers_are_exactly_resolved_before_payload_creation(
    facade: OfflineTicketPilotFacade,
) -> None:
    command = facade._draft_to_command(
        TicketDraft(
            project="DAH",
            issue_type="Story",
            summary="Resolved identifiers",
            values={
                "components": ["BI"],
                "assignee": "alex.example",
                "participants": ["demo.user", "sam.example"],
                "epic_link": "DAH-90003",
                "parent_link": "DAH-90002",
            },
        )
    )
    assert command.fields["assignee"] == AssigneeSelection(
        AssigneeMode.USER,
        user_id="alex.example",
    )
    assert command.fields["participants"] == ["demo.user", "sam.example"]
    assert command.fields["epic_link"] == "DAH-90003"
    assert command.fields["parent_link"] == "DAH-90002"

    invalid = TicketDraft(
        project="DAH",
        issue_type="Story",
        summary="Raw identifier blocked",
        values={"components": ["BI"], "assignee": "not-a-real-account"},
    )
    preview = facade.preview((invalid,))[0]
    assert preview.valid is False
    assert facade._previews[preview.preview_id].payload == {}


def test_cache_ttl_and_url_or_board_changes_apply_immediately_and_expire_previews(
    facade: OfflineTicketPilotFacade,
) -> None:
    facade.save_settings(replace(facade.load_settings(), cache_ttl_hours=1))
    entry = facade._store.put_metadata("ttl-check", "priorities", [])
    assert (entry.expires_at - entry.fetched_at).total_seconds() == 3600

    url_preview = facade.preview(
        (TicketDraft(project="DAH", issue_type="Incident", summary="Old URL"),)
    )[0]
    setup = replace(facade.load_setup(), jira_url="https://jira.example.invalid")
    assert facade.save_setup(setup, "").ok
    assert facade.execute(
        ExecutionRequest((url_preview.preview_id,), confirmed=False, dry_run=True)
    )[0].status == "Fehler"

    board_preview = facade.preview(
        (TicketDraft(project="DAH", issue_type="Incident", summary="Old board"),)
    )[0]
    facade.save_settings(replace(facade.load_settings(), selected_board="demo:board:1"))
    assert facade.execute(
        ExecutionRequest((board_preview.preview_id,), confirmed=False, dry_run=True)
    )[0].status == "Fehler"


def test_update_timeout_is_uncertain_and_cached_without_second_write(
    facade: OfflineTicketPilotFacade,
) -> None:
    facade.save_settings(replace(facade.load_settings(), dry_run=False))
    draft = facade.load_ticket("DAH-90001")
    draft.summary = "Timeout after update"
    preview = facade.preview((draft,))[0]
    calls = 0

    def timeout_update(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise TimeoutError("response lost after update")

    facade._gateway.update_issue = timeout_update
    request = ExecutionRequest((preview.preview_id,), confirmed=True, dry_run=False)
    first = facade.execute(request)[0]
    second = facade.execute(request)[0]
    assert first.status == "Unklarer Zustand"
    assert first.retry_allowed is False
    assert first.uncertain is True
    assert second == first
    assert calls == 1
