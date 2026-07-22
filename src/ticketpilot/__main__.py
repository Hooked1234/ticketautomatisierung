"""TicketPilot executable entry point."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from ticketpilot.bootstrap import create_facade


def main(argv: Sequence[str] | None = None) -> int:
    try:
        from ticketpilot.ui.app import create_application
    except ImportError as error:
        print(
            "TicketPilot benötigt PySide6. Installieren Sie die Laufzeit mit "
            "`python -m pip install -e .`.",
            file=sys.stderr,
        )
        raise SystemExit(2) from error

    facade = create_facade()
    app = create_application(facade, argv)
    window = app.__dict__["_ticketpilot_main_window"]

    def close_services_safely() -> None:
        # Programmatic quit and Windows shutdown can bypass MainWindow's normal
        # close guard.  Never close SQLite underneath a running worker.
        if window.prepare_shutdown():
            facade.close()

    app.aboutToQuit.connect(close_services_safely)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
