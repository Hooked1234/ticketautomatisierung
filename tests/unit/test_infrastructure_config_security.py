from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ticketpilot.infrastructure.config import AppConfig, ConfigError, load_config
from ticketpilot.infrastructure.security import (
    REDACTED,
    SensitiveDataError,
    assert_no_secret_fields,
    configure_secure_file_logging,
    redact_text,
    safe_json_dumps,
    sanitize,
)


class AppConfigTests(unittest.TestCase):
    def test_secure_defaults_are_dry_run_optional_url_and_dah_only(self) -> None:
        config = load_config(environ={})

        self.assertTrue(config.dry_run)
        self.assertIsNone(config.jira_url)
        self.assertEqual(config.project_allowlist, ("DAH",))
        self.assertEqual(config.selected_project, "DAH")
        self.assertEqual(config.cache_ttl_hours, 24)
        self.assertTrue(config.demo_mode)

    def test_mapping_is_normalized_and_selected_project_must_be_allowed(self) -> None:
        config = AppConfig.from_mapping(
            {
                "DRY_RUN": "false",
                "JIRA_URL": "https://jira.example.invalid/root/",
                "PROJECT_ALLOWLIST": ["dah", "team2", "DAH"],
                "SELECTED_PROJECT": "team2",
                "CACHE_TTL_HOURS": "12",
            }
        )

        self.assertFalse(config.dry_run)
        self.assertEqual(config.jira_url, "https://jira.example.invalid/root")
        self.assertEqual(config.project_allowlist, ("DAH", "TEAM2"))
        self.assertEqual(config.selected_project, "TEAM2")
        self.assertEqual(config.cache_ttl_hours, 12)

        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"project_allowlist": ["DAH"], "selected_project": "OPS"})

    def test_environment_legacy_project_key_is_safe_but_token_is_ignored(self) -> None:
        config = load_config(
            environ={
                "PROJECT_KEY": "dah",
                "DRY_RUN": "true",
                "JIRA_TOKEN": "must-never-enter-config",
            }
        )

        self.assertEqual(config.project_allowlist, ("DAH",))
        self.assertNotIn("token", json.dumps(config.to_public_dict()).lower())
        self.assertNotIn("must-never-enter-config", json.dumps(config.to_public_dict()))

    def test_yaml_subset_and_secret_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                "dry_run: true\n"
                "jira_url: https://jira.example.invalid\n"
                "project_allowlist:\n"
                "  - DAH\n"
                "  - TEAM2\n"
                "selected_project: TEAM2\n",
                encoding="utf-8",
            )
            config = load_config(config_path, environ={})
            self.assertEqual(config.project_allowlist, ("DAH", "TEAM2"))

            config_path.write_text("jira_token: secret\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(config_path, environ={})

    def test_url_must_not_embed_credentials_or_query(self) -> None:
        for url in (
            "jira.invalid",
            "https://user:pass@jira.invalid",
            "https://jira.invalid?token=value",
        ):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                AppConfig(jira_url=url)


class SecurityTests(unittest.TestCase):
    def test_nested_sensitive_keys_and_auth_strings_are_redacted(self) -> None:
        secret = "SuperSecret-12345"
        value = {
            "headers": {"Authorization": f"Bearer {secret}"},
            "safe": [f"authorization=Basic {secret}", "kept"],
        }

        sanitized = sanitize(value)
        encoded = safe_json_dumps(value)

        self.assertEqual(sanitized["headers"]["Authorization"], REDACTED)
        self.assertNotIn(secret, encoded)
        self.assertIn("kept", encoded)

    def test_persistence_guard_rejects_secret_keys_recursively(self) -> None:
        with self.assertRaises(SensitiveDataError):
            assert_no_secret_fields({"nested": [{"client_secret": "value"}]})

    def test_text_redaction_covers_auth_headers_urls_queries_and_jwts(self) -> None:
        raw = (
            "Authorization: Bearer abcdefghijk "
            "https://alice:pw@example.invalid/path?api_key=xyz "
            "eyJabcdefghij.abcdefghijkl.abcdefghijkl"
        )
        result = redact_text(raw)

        for secret in ("abcdefghijk", "alice", "pw", "xyz", "eyJabcdefghij"):
            self.assertNotIn(secret, result)
        self.assertIn(REDACTED, result)

    def test_secure_file_logger_writes_bounded_json_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ticketpilot.jsonl"
            logger = configure_secure_file_logging(
                path,
                logger_name="ticketpilot.test.secure",
                max_bytes=10_000,
                backup_count=1,
            )
            logger.info(
                "request Authorization: Bearer never-write-this",
                extra={"token": "also-secret", "operation": "metadata.refresh"},
            )
            for handler in logger.handlers:
                handler.flush()
                handler.close()
            logger.handlers.clear()

            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)
            self.assertNotIn("never-write-this", text)
            self.assertNotIn("also-secret", text)
            self.assertEqual(payload["context"]["token"], REDACTED)
            self.assertEqual(payload["context"]["operation"], "metadata.refresh")
            self.assertEqual(payload["level"], "INFO")


if __name__ == "__main__":
    unittest.main()
