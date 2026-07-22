"""TicketPilot desktop UI with a safe optional PySide6 import guard."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .contracts import TicketPilotFacade, UnavailableFacade

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

PYSIDE6_AVAILABLE = False
PYSIDE6_IMPORT_ERROR: ModuleNotFoundError | None = None

try:
    from .app import create_application, run
    from .main_window import MainWindow
except ModuleNotFoundError as exc:
    if exc.name != "PySide6" and not (exc.name or "").startswith("PySide6."):
        raise
    PYSIDE6_IMPORT_ERROR = exc

    def create_application(
        facade: TicketPilotFacade | None = None,
        argv: Sequence[str] | None = None,
    ) -> QApplication:
        raise RuntimeError(
            "Für die Desktopoberfläche wird PySide6 benötigt. "
            "Die UI-unabhängigen TicketPilot-Module bleiben ohne PySide6 nutzbar."
        ) from PYSIDE6_IMPORT_ERROR

    def run(
        facade: TicketPilotFacade | None = None,
        argv: Sequence[str] | None = None,
    ) -> int:
        create_application(facade, argv)
        return 1

    MainWindow = None  # type: ignore[misc,assignment]
else:
    PYSIDE6_AVAILABLE = True


__all__ = [
    "MainWindow",
    "PYSIDE6_AVAILABLE",
    "PYSIDE6_IMPORT_ERROR",
    "TicketPilotFacade",
    "UnavailableFacade",
    "create_application",
    "run",
]
