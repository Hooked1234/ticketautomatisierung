"""Stable desktop composition entry points."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import cast

from PySide6.QtWidgets import QApplication

from .contracts import TicketPilotFacade, UnavailableFacade
from .main_window import MainWindow
from .theme import configure_application


def create_application(
    facade: TicketPilotFacade | None = None,
    argv: Sequence[str] | None = None,
) -> QApplication:
    """Create, style and show TicketPilot.

    The returned ``QApplication`` owns the window via the private
    ``_ticketpilot_main_window`` attribute, preventing premature collection.
    Callers normally finish with ``create_application(facade).exec()``.
    """

    existing = QApplication.instance()
    app = cast(QApplication, existing) if existing is not None else QApplication(list(argv) if argv is not None else sys.argv)
    configure_application(app)
    window = MainWindow(facade or UnavailableFacade())
    app.__dict__["_ticketpilot_main_window"] = window
    window.show()
    return app


def run(facade: TicketPilotFacade | None = None, argv: Sequence[str] | None = None) -> int:
    return create_application(facade, argv).exec()
