from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from config_loader import ConfigError, load_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


class ConfigLoaderTests(unittest.TestCase):
    def load(self, environment=None, require_credentials=False, env_path=None):
        return load_config(
            config_path=CONFIG_PATH,
            env_path=env_path,
            environ={} if environment is None else environment,
            require_credentials=require_credentials,
        )

    def test_config_yaml_loads_dah_with_safe_defaults(self):
        config = self.load()
        self.assertEqual(config.allowed_project_keys, ("DAH",))
        self.assertEqual(config.project_key, "DAH")
        self.assertTrue(config.dry_run)
        self.assertTrue(config.safety.dry_run_default)
        self.assertFalse(config.safety.allow_delete)
        self.assertEqual(config.metadata.refresh_after_hours, 24)

    def test_legacy_environment_values_remain_supported(self):
        environment = {
            "JIRA_URL": "https://jira.example.test/",
            "JIRA_TOKEN": "unit-test-token",
            "PROJECT_KEY": "dah",
            "EXCEL_FILE": "legacy.xlsm",
            "DRY_RUN": "false",
        }
        config = self.load(environment, require_credentials=True)
        legacy = config.to_legacy_dict()
        self.assertEqual(legacy["JIRA_URL"], "https://jira.example.test")
        self.assertEqual(legacy["PROJECT_KEY"], "DAH")
        self.assertEqual(legacy["EXCEL_FILE"], "legacy.xlsm")
        self.assertEqual(legacy["DRY_RUN"], "false")
        self.assertNotIn(environment["JIRA_TOKEN"], repr(config))

    def test_dotenv_file_is_loaded_without_os_environment(self):
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "JIRA_URL=https://jira.example.test\n"
                "JIRA_TOKEN=local-placeholder-token\n"
                "PROJECT_KEY=DAH\n"
                "EXCEL_FILE=tickets.xlsm\n"
                "DRY_RUN=true\n",
                encoding="utf-8",
            )
            config = self.load({}, require_credentials=True, env_path=env_path)
        self.assertEqual(config.project_key, "DAH")
        self.assertTrue(config.dry_run)

    def test_unknown_legacy_project_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "nicht freigegeben"):
            self.load({"PROJECT_KEY": "OTHER"})

    def test_missing_credentials_are_named_but_not_printed(self):
        with self.assertRaisesRegex(ConfigError, "JIRA_URL, JIRA_TOKEN"):
            self.load({}, require_credentials=True)

    def test_invalid_dry_run_value_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "DRY_RUN muss"):
            self.load({"DRY_RUN": "sometimes"})

    def test_unsafe_yaml_switch_is_rejected(self):
        source = CONFIG_PATH.read_text(encoding="utf-8")
        unsafe = source.replace("allow_delete: false", "allow_delete: true")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(unsafe, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "allow_delete"):
                load_config(
                    config_path=path,
                    env_path=None,
                    environ={},
                    require_credentials=False,
                )

    def test_yaml_parser_error_does_not_echo_file_contents(self):
        secret = "must-not-appear-in-error"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(f"jira:\n  bad: [{secret}\n", encoding="utf-8")
            with self.assertRaises(ConfigError) as context:
                load_config(
                    config_path=path,
                    env_path=None,
                    environ={},
                    require_credentials=False,
                )
        self.assertNotIn(secret, str(context.exception))

    def test_config_file_contains_only_dah(self):
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            raw["jira"]["allowed_projects"],
            [{"key": "DAH", "name": "Data & Analytics Hub"}],
        )

    def test_env_example_has_no_productive_url_or_real_token(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn("jira.dfd-hamburg.de", example)
        self.assertIn("jira.example.invalid", example)
        self.assertIn("DRY_RUN=true", example)
        self.assertNotIn("unit-test-token", example)


if __name__ == "__main__":
    unittest.main()
