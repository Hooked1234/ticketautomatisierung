"""Non-blocking task execution and write-operation interlocks."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


def register_process_secret(secret: str) -> None:
    """Register a process-local secret before it can cross an error boundary."""

    if not secret:
        return
    try:
        from ticketpilot.infrastructure.security import register_secret
    except ImportError:
        return
    register_secret(secret)


def _redact_error(error: BaseException) -> str:
    try:
        from ticketpilot.infrastructure.security import safe_error_message
    except ImportError:  # Reduced embedding environment without infrastructure.
        from ticketpilot.application.errors import safe_error_message as application_safe_error

        return application_safe_error(error)
    return safe_error_message(error)


class CancellationToken:
    """Cooperative cancellation for read-only operations.

    Write operations never expose a cancel path because stopping a request after
    transmission could leave an ambiguous server state.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelledError("Vorgang wurde abgebrochen.")


class TaskCancelledError(RuntimeError):
    pass


class _WorkerSignals(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str, str)
    finished = Signal(str)
    progress = Signal(str, int, str)


class _Worker(QRunnable):
    def __init__(
        self,
        key: str,
        operation: Callable[[CancellationToken, Callable[[int, str], None]], Any],
        token: CancellationToken,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._key = key
        self._operation = operation
        self._token = token
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        def emit_progress(value: int, message: str = "") -> None:
            self.signals.progress.emit(
                self._key,
                max(0, min(100, int(value))),
                str(message),
            )

        try:
            result = self._operation(self._token, emit_progress)
        except TaskCancelledError:
            self.signals.failed.emit(self._key, "Abgebrochen", "")
        except Exception as exc:
            # Defense in depth: never trust an exception crossing a worker
            # boundary to have been sanitized by its originating service.
            try:
                message = _redact_error(exc)
            except Exception:
                message = "Vorgang fehlgeschlagen; unsichere Fehlerdetails wurden verworfen."
            self.signals.failed.emit(self._key, message, "")
        else:
            self.signals.succeeded.emit(self._key, result)
        finally:
            self.signals.finished.emit(self._key)


@dataclass(slots=True)
class _RunningTask:
    token: CancellationToken
    write: bool
    cancellable: bool
    worker: _Worker


class TaskRunner(QObject):
    """Runs blocking facade calls while guarding duplicate write submission."""

    task_started = Signal(str, bool)
    task_succeeded = Signal(str, object)
    task_failed = Signal(str, str, str)
    task_finished = Signal(str)
    task_progress = Signal(str, int, str)
    busy_changed = Signal(bool)
    write_busy_changed = Signal(bool)
    duplicate_rejected = Signal(str)

    def __init__(self, parent: QObject | None = None, pool: QThreadPool | None = None) -> None:
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._running: dict[str, _RunningTask] = {}
        self._write_count = 0

    @property
    def busy(self) -> bool:
        return bool(self._running)

    @property
    def write_busy(self) -> bool:
        return self._write_count > 0

    def is_running(self, key: str) -> bool:
        return key in self._running

    def submit(
        self,
        key: str,
        operation: Callable[[CancellationToken, Callable[[int, str], None]], Any],
        *,
        write: bool = False,
        cancellable: bool = True,
    ) -> bool:
        """Schedule ``operation`` once.

        A second click with the same operation key is rejected.  While any write
        is running, no additional write can start, even with a different key.
        """

        if key in self._running or (write and self.write_busy):
            self.duplicate_rejected.emit(key)
            return False
        token = CancellationToken()
        worker = _Worker(key, operation, token)
        worker.signals.succeeded.connect(self.task_succeeded)
        worker.signals.failed.connect(self.task_failed)
        worker.signals.finished.connect(self._finish)
        worker.signals.progress.connect(self.task_progress)
        was_busy = self.busy
        was_write_busy = self.write_busy
        self._running[key] = _RunningTask(token, write, cancellable and not write, worker)
        if write:
            self._write_count += 1
        if not was_busy:
            self.busy_changed.emit(True)
        if not was_write_busy and self.write_busy:
            self.write_busy_changed.emit(True)
        self.task_started.emit(key, write)
        self._pool.start(worker)
        return True

    def cancel(self, key: str) -> bool:
        running = self._running.get(key)
        if running is None or not running.cancellable or running.write:
            return False
        running.token.cancel()
        return True

    def shutdown(self, timeout_ms: int = 60_000) -> bool:
        """Cancel safe reads and drain workers before persistence is closed.

        Writes are deliberately never cancelled.  Returning ``False`` tells
        the composition root to leave shared persistence open rather than
        racing a still-running write during operating-system shutdown.
        """

        for running in tuple(self._running.values()):
            if running.cancellable and not running.write:
                running.token.cancel()
        return bool(self._pool.waitForDone(max(0, timeout_ms)))

    @Slot(str)
    def _finish(self, key: str) -> None:
        running = self._running.pop(key, None)
        if running is None:
            return
        if running.write:
            self._write_count = max(0, self._write_count - 1)
            if not self.write_busy:
                self.write_busy_changed.emit(False)
        self.task_finished.emit(key)
        if not self.busy:
            self.busy_changed.emit(False)
