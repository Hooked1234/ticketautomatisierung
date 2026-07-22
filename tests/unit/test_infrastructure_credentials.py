from __future__ import annotations

import unittest

from ticketpilot.application.ports import CredentialStore as ApplicationCredentialStore
from ticketpilot.infrastructure.credentials import (
    KeyringCredentialStore,
    MemoryCredentialStore,
    create_credential_store,
)
from ticketpilot.infrastructure.security import redact_text


class WindowsSecureBackend:
    priority = 5.0


class PlaintextBackend:
    priority = 5.0


class FakeKeyring:
    def __init__(self, backend: object | None = None) -> None:
        self.backend = backend or WindowsSecureBackend()
        self.values: dict[tuple[str, str], str] = {}

    def get_keyring(self) -> object:
        return self.backend

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        del self.values[(service_name, username)]


class CredentialStoreTests(unittest.TestCase):
    def test_memory_store_is_process_only_and_implements_application_port(self) -> None:
        store = MemoryCredentialStore()
        self.assertFalse(store.persistent)
        self.assertIsInstance(store, ApplicationCredentialStore)

        store.save("TicketPilot/Jira", "felix", "very-secret")
        self.assertEqual(store.load("TicketPilot/Jira", "felix"), "very-secret")
        store.delete("TicketPilot/Jira", "felix")
        self.assertIsNone(store.load("TicketPilot/Jira", "felix"))

        store.set_token("jira-account", "another-secret")
        self.assertEqual(store.get_token("jira-account"), "another-secret")
        self.assertNotIn("another-secret", redact_text("unlabelled another-secret value"))
        self.assertTrue(store.delete_token("jira-account"))

    def test_non_windows_and_missing_keyring_fall_back_only_to_memory(self) -> None:
        linux_store = create_credential_store(platform_name="linux")
        missing_store = create_credential_store(
            platform_name="win32",
            keyring_loader=lambda: (_ for _ in ()).throw(ImportError("not installed")),
        )

        self.assertIsInstance(linux_store, MemoryCredentialStore)
        self.assertIsInstance(missing_store, MemoryCredentialStore)
        self.assertFalse(linux_store.persistent)
        self.assertFalse(missing_store.persistent)

    def test_secure_windows_keyring_is_selected_and_namespaced(self) -> None:
        fake = FakeKeyring()
        store = create_credential_store(
            platform_name="win32",
            service_name="TicketPilot",
            keyring_loader=lambda: fake,
        )

        self.assertIsInstance(store, KeyringCredentialStore)
        self.assertTrue(store.persistent)
        store.save("Jira", "felix", "secret-value")
        self.assertEqual(fake.values[("TicketPilot:Jira", "felix")], "secret-value")
        self.assertEqual(store.load("Jira", "felix"), "secret-value")
        store.delete("Jira", "felix")
        self.assertNotIn(("TicketPilot:Jira", "felix"), fake.values)

    def test_plaintext_keyring_backend_is_rejected_for_memory_fallback(self) -> None:
        store = create_credential_store(
            platform_name="win32",
            keyring_loader=lambda: FakeKeyring(PlaintextBackend()),
        )

        self.assertIsInstance(store, MemoryCredentialStore)
        self.assertFalse(store.persistent)

    def test_empty_credentials_are_rejected_without_exposing_values(self) -> None:
        store = MemoryCredentialStore()
        with self.assertRaises(ValueError):
            store.save("Jira", "felix", "  ")
        with self.assertRaises(ValueError):
            store.save("", "felix", "secret")


if __name__ == "__main__":
    unittest.main()
