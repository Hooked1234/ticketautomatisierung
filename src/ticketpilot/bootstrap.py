"""Application composition root and platform paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ticketpilot.facade import OfflineTicketPilotFacade
from ticketpilot.infrastructure import (
    SQLiteStore,
    configure_secure_file_logging,
    create_credential_store,
)


def default_data_directory() -> Path:
    """Return a per-user data directory without writing to the install folder."""

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "TicketPilot"
        return Path.home() / "AppData" / "Local" / "TicketPilot"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "ticketpilot"


def create_facade(data_directory: str | Path | None = None) -> OfflineTicketPilotFacade:
    directory = Path(data_directory) if data_directory is not None else default_data_directory()
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    logger = configure_secure_file_logging(directory / "ticketpilot.log")
    store = SQLiteStore(directory / "ticketpilot.sqlite3")
    credentials = create_credential_store(service_name="TicketPilot")
    facade = OfflineTicketPilotFacade(
        store,
        data_directory=directory,
        credentials=credentials,
    )
    logger.info("TicketPilot initialized", extra={"mode": "offline-demo"})
    return facade


__all__ = ["create_facade", "default_data_directory"]
