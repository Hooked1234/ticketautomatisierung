from __future__ import annotations

from pathlib import Path

from ticketpilot.bootstrap import create_facade


def test_composition_root_uses_explicit_data_directory_and_secure_local_log(
    tmp_path: Path,
) -> None:
    facade = create_facade(tmp_path / "TicketPilot Data With Spaces")
    try:
        snapshot = facade.startup_snapshot()
        assert snapshot.online is False
        assert snapshot.dry_run is True
        assert (tmp_path / "TicketPilot Data With Spaces" / "ticketpilot.sqlite3").is_file()
        log_path = tmp_path / "TicketPilot Data With Spaces" / "ticketpilot.log"
        assert log_path.is_file()
        assert "offline-demo" in log_path.read_text(encoding="utf-8")
    finally:
        facade.close()
