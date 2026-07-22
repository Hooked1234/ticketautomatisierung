from __future__ import annotations

from pathlib import Path

from ticketpilot.facade import OfflineTicketPilotFacade
from ticketpilot.infrastructure import MemoryCredentialStore, SQLiteStore
from ticketpilot.ui.app import create_application


def test_complete_shell_starts_headless_and_loads_offline_session(qtbot, tmp_path: Path) -> None:
    facade = OfflineTicketPilotFacade(
        SQLiteStore(tmp_path / "ui.sqlite3"),
        data_directory=tmp_path,
        credentials=MemoryCredentialStore(),
    )
    app = create_application(facade, ["ticketpilot-ui-test"])
    window = app._ticketpilot_main_window
    qtbot.addWidget(window)

    qtbot.waitUntil(lambda: window.session.user_account == "demo.user", timeout=5_000)
    assert window.windowTitle().startswith("TicketPilot")
    assert window.navigation.count() == 7
    assert window.session.dry_run is True
    assert window.session.online is False
    assert [window.navigation.item(index).text() for index in range(7)] == [
        "Status",
        "Tickets",
        "Ergebnisse & Konflikte",
        "Dashboard",
        "Kommentare",
        "Einstellungen & Audit",
        "Einrichtung",
    ]

    for index in range(window.navigation.count()):
        window.navigation.setCurrentRow(index)
        qtbot.wait(20)
        window.refresh_current_page()
        qtbot.waitUntil(lambda: not window.tasks.busy, timeout=5_000)
        assert window.stack.currentIndex() == index

    qtbot.waitUntil(lambda: not window.tasks.busy, timeout=5_000)
    window.close()
    facade.close()
