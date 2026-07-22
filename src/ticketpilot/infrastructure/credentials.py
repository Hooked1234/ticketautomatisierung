"""Credential abstraction with a secure Windows keyring and memory-only fallback."""

from __future__ import annotations

import importlib
import sys
import threading
from collections.abc import Callable
from typing import Protocol, cast, runtime_checkable

from .security import register_secret


class CredentialError(RuntimeError):
    """Base class for safe credential adapter failures."""


class CredentialStoreUnavailable(CredentialError):
    """Raised when the operating-system credential backend is unavailable."""


@runtime_checkable
class CredentialStore(Protocol):
    """Minimal secret-store contract consumed by the application layer."""

    persistent: bool

    def get_secret(self, account: str) -> str | None: ...

    def set_secret(self, account: str, secret: str) -> None: ...

    def delete_secret(self, account: str) -> bool: ...

    def save(self, service: str, username: str, secret: str) -> None: ...

    def load(self, service: str, username: str) -> str | None: ...

    def delete(self, service: str, username: str) -> None: ...


class MemoryCredentialStore:
    """Process-local fallback; values are never serialized or logged."""

    persistent = False

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self._lock = threading.RLock()

    def get_secret(self, account: str) -> str | None:
        key = _account(account)
        with self._lock:
            value = self._secrets.get(key)
        if value is not None:
            register_secret(value)
        return value

    def set_secret(self, account: str, secret: str) -> None:
        key = _account(account)
        value = _secret(secret)
        register_secret(value)
        with self._lock:
            self._secrets[key] = value

    def delete_secret(self, account: str) -> bool:
        key = _account(account)
        with self._lock:
            existed = key in self._secrets
            self._secrets.pop(key, None)
            return existed

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def save(self, service: str, username: str, secret: str) -> None:
        """Implement ``application.ports.CredentialStore`` without persistence."""

        self.set_secret(_composite_account(service, username), secret)

    def load(self, service: str, username: str) -> str | None:
        return self.get_secret(_composite_account(service, username))

    def delete(self, service: str, username: str) -> None:
        self.delete_secret(_composite_account(service, username))

    # Token aliases keep the UI simple without widening the persistence API.
    get_token = get_secret
    set_token = set_secret
    delete_token = delete_secret


class _Backend(Protocol):
    priority: float


class _KeyringModule(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...

    def get_keyring(self) -> _Backend: ...


class KeyringCredentialStore:
    """Adapter for the Windows Credential Manager through ``keyring``."""

    persistent = True

    def __init__(self, service_name: str, keyring_module: _KeyringModule) -> None:
        self.service_name = _account(service_name)
        self._keyring = keyring_module
        _validate_keyring_backend(keyring_module)

    def get_secret(self, account: str) -> str | None:
        try:
            value = self._keyring.get_password(self.service_name, _account(account))
            if value is not None:
                register_secret(value)
            return value
        except Exception as error:
            raise CredentialError("Credential Manager value could not be read") from error

    def set_secret(self, account: str, secret: str) -> None:
        value = _secret(secret)
        try:
            self._keyring.set_password(self.service_name, _account(account), value)
            register_secret(value)
        except Exception as error:
            raise CredentialError("Credential Manager value could not be stored") from error

    def delete_secret(self, account: str) -> bool:
        key = _account(account)
        try:
            if self._keyring.get_password(self.service_name, key) is None:
                return False
            self._keyring.delete_password(self.service_name, key)
            return True
        except Exception as error:
            raise CredentialError("Credential Manager value could not be deleted") from error

    def save(self, service: str, username: str, secret: str) -> None:
        actual_service = _service_name(self.service_name, service)
        value = _secret(secret)
        try:
            self._keyring.set_password(actual_service, _account(username), value)
            register_secret(value)
        except Exception as error:
            raise CredentialError("Credential Manager value could not be stored") from error

    def load(self, service: str, username: str) -> str | None:
        actual_service = _service_name(self.service_name, service)
        try:
            value = self._keyring.get_password(actual_service, _account(username))
            if value is not None:
                register_secret(value)
            return value
        except Exception as error:
            raise CredentialError("Credential Manager value could not be read") from error

    def delete(self, service: str, username: str) -> None:
        actual_service = _service_name(self.service_name, service)
        user = _account(username)
        try:
            if self._keyring.get_password(actual_service, user) is not None:
                self._keyring.delete_password(actual_service, user)
        except Exception as error:
            raise CredentialError("Credential Manager value could not be deleted") from error

    get_token = get_secret
    set_token = set_secret
    delete_token = delete_secret


WindowsCredentialStore = KeyringCredentialStore


def create_credential_store(
    *,
    service_name: str = "TicketPilot",
    platform_name: str | None = None,
    keyring_loader: Callable[[], _KeyringModule] | None = None,
) -> CredentialStore:
    """Return Windows keyring when safe and available, otherwise memory only.

    There is intentionally no plaintext file, environment or SQLite fallback.
    ``JIRA_TOKEN`` migration can be implemented by application code as a one-time
    read followed by a call to this adapter, but this module never writes it back.
    """

    platform_value = sys.platform if platform_name is None else platform_name
    if not platform_value.lower().startswith("win"):
        return MemoryCredentialStore()
    try:
        if keyring_loader is None:
            loaded = importlib.import_module("keyring")
            keyring_module = cast(_KeyringModule, loaded)
        else:
            keyring_module = keyring_loader()
        return KeyringCredentialStore(service_name, keyring_module)
    except (ImportError, CredentialStoreUnavailable, RuntimeError):
        return MemoryCredentialStore()


def _validate_keyring_backend(module: _KeyringModule) -> None:
    try:
        backend = module.get_keyring()
        priority = float(backend.priority)
    except Exception as error:
        raise CredentialStoreUnavailable("No usable Windows Credential Manager backend") from error
    identity = f"{type(backend).__module__}.{type(backend).__name__}".lower()
    insecure_markers = ("plaintext", "null", "fail", "chainer")
    if priority <= 0 or any(marker in identity for marker in insecure_markers):
        raise CredentialStoreUnavailable("No secure Windows Credential Manager backend")
    if "windows" not in identity and "winvault" not in identity:
        raise CredentialStoreUnavailable("Backend is not the Windows Credential Manager")


def _account(value: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError("Credential account must not be empty")
    if "\n" in result or "\r" in result:
        raise ValueError("Credential account contains invalid characters")
    return result


def _secret(value: str) -> str:
    result = str(value)
    if not result.strip():
        raise ValueError("Credential value must not be empty")
    return result


def _composite_account(service: str, username: str) -> str:
    return f"{_account(service)}::{_account(username)}"


def _service_name(namespace: str, service: str) -> str:
    normalized = _account(service)
    return namespace if normalized == namespace else f"{namespace}:{normalized}"


__all__ = [
    "CredentialError",
    "CredentialStore",
    "CredentialStoreUnavailable",
    "KeyringCredentialStore",
    "MemoryCredentialStore",
    "WindowsCredentialStore",
    "create_credential_store",
]
