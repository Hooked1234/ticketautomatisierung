"""Single source of truth for local, non-secret application configuration."""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .security import SensitiveDataError, assert_no_secret_fields, is_sensitive_key

DEFAULT_PROJECT = "DAH"
DEFAULT_CACHE_TTL_HOURS = 24
_PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "ja"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "nein"})


class ConfigError(ValueError):
    """Raised for invalid or unsafe local configuration."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated configuration containing no credentials."""

    dry_run: bool = True
    jira_url: str | None = None
    project_allowlist: tuple[str, ...] = (DEFAULT_PROJECT,)
    cache_ttl_hours: int = DEFAULT_CACHE_TTL_HOURS
    demo_mode: bool = True
    selected_project: str = DEFAULT_PROJECT

    def __post_init__(self) -> None:
        normalized_url = _normalize_jira_url(self.jira_url)
        normalized_projects = _normalize_projects(self.project_allowlist)
        selected_project = self.selected_project.strip().upper()
        if selected_project not in normalized_projects:
            raise ConfigError("Selected project must be present in the project allowlist")
        if not 1 <= self.cache_ttl_hours <= 168:
            raise ConfigError("Cache TTL must be between 1 and 168 hours")
        object.__setattr__(self, "jira_url", normalized_url)
        object.__setattr__(self, "project_allowlist", normalized_projects)
        object.__setattr__(self, "selected_project", selected_project)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any] | None = None) -> AppConfig:
        raw = dict(mapping or {})
        try:
            assert_no_secret_fields(raw)
        except SensitiveDataError as error:
            raise ConfigError(str(error)) from error

        lowered = {str(key).strip().lower(): value for key, value in raw.items()}
        projects = lowered.get(
            "project_allowlist",
            lowered.get("allowed_projects", lowered.get("project_key", (DEFAULT_PROJECT,))),
        )
        if isinstance(projects, str):
            projects = tuple(part.strip() for part in projects.split(",") if part.strip())
        elif isinstance(projects, (list, tuple, set, frozenset)):
            projects = tuple(str(part) for part in projects)
        else:
            raise ConfigError("Project allowlist must be a list or comma-separated string")

        selected = lowered.get("selected_project", lowered.get("project_key"))
        normalized_projects = _normalize_projects(projects)
        if selected is None:
            selected = normalized_projects[0]
        try:
            cache_ttl = int(lowered.get("cache_ttl_hours", DEFAULT_CACHE_TTL_HOURS))
        except (TypeError, ValueError) as error:
            raise ConfigError("Cache TTL must be an integer") from error

        return cls(
            dry_run=_parse_bool(lowered.get("dry_run", True), "dry_run"),
            jira_url=lowered.get("jira_url") or None,
            project_allowlist=normalized_projects,
            cache_ttl_hours=cache_ttl,
            demo_mode=_parse_bool(lowered.get("demo_mode", True), "demo_mode"),
            selected_project=str(selected),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "jira_url": self.jira_url,
            "project_allowlist": list(self.project_allowlist),
            "cache_ttl_hours": self.cache_ttl_hours,
            "demo_mode": self.demo_mode,
            "selected_project": self.selected_project,
        }


def load_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load non-secret configuration and apply safe environment overrides.

    JSON, TOML and the small top-level YAML subset used by TicketPilot are
    accepted without adding a YAML runtime dependency.  Environment tokens are
    intentionally ignored; credential migration belongs to a credential adapter.
    """

    values: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        if config_path.exists():
            values.update(_read_config_file(config_path))
    env = os.environ if environ is None else environ
    overrides = {
        "jira_url": env.get("JIRA_URL"),
        "dry_run": env.get("DRY_RUN"),
        "project_allowlist": env.get("PROJECT_ALLOWLIST") or env.get("PROJECT_KEY"),
        "cache_ttl_hours": env.get("CACHE_TTL_HOURS"),
        "demo_mode": env.get("DEMO_MODE"),
        "selected_project": env.get("SELECTED_PROJECT") or env.get("PROJECT_KEY"),
    }
    values.update({key: value for key, value in overrides.items() if value is not None})
    return AppConfig.from_mapping(values)


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"Configuration could not be read: {path.name}") from error
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            parsed = json.loads(text)
        elif suffix == ".toml":
            parsed = tomllib.loads(text)
            parsed = parsed.get("ticketpilot", parsed)
        elif suffix in {".yaml", ".yml"}:
            parsed = _parse_simple_yaml(text)
        else:
            raise ConfigError("Configuration must be JSON, TOML, YAML or YML")
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"Invalid configuration syntax in {path.name}") from error
    if not isinstance(parsed, dict):
        raise ConfigError("Configuration root must be an object")
    if any(is_sensitive_key(key) for key in parsed):
        raise ConfigError("Credential fields are not allowed in the configuration file")
    return parsed


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the deliberately tiny, non-nested TicketPilot YAML format."""

    result: dict[str, Any] = {}
    current_list: str | None = None
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("-"):
            if current_list is None:
                raise ConfigError(f"Unexpected YAML list entry on line {number}")
            cast_list = result[current_list]
            if not isinstance(cast_list, list):  # pragma: no cover - invariant guard
                raise ConfigError(f"Invalid YAML list on line {number}")
            cast_list.append(_parse_scalar(stripped[1:].strip()))
            continue
        if line[:1].isspace() or ":" not in stripped:
            raise ConfigError(f"Only top-level YAML keys are supported (line {number})")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if is_sensitive_key(key):
            raise ConfigError("Credential fields are not allowed in the configuration file")
        raw_value = raw_value.strip()
        if not raw_value:
            result[key] = []
            current_list = key
        else:
            result[key] = _parse_scalar(raw_value)
            current_list = None
    return result


def _parse_scalar(value: str) -> Any:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    lowered = stripped.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1]
        return [_parse_scalar(part) for part in inner.split(",") if part.strip()]
    if re.fullmatch(r"-?\d+", stripped):
        return int(stripped)
    return stripped


def _normalize_jira_url(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError as error:
        raise ConfigError("Jira URL is invalid") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("Jira URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("Jira URL must not contain credentials, a query or a fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _normalize_projects(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ConfigError("Project allowlist must contain project keys")
    normalized: list[str] = []
    for value in values:
        project = str(value).strip().upper()
        if not _PROJECT_KEY_RE.fullmatch(project):
            raise ConfigError(f"Invalid Jira project key: {project!r}")
        if project not in normalized:
            normalized.append(project)
    if not normalized:
        raise ConfigError("Project allowlist must not be empty")
    return tuple(normalized)


def _parse_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    raise ConfigError(f"{name} must be a boolean")


__all__ = [
    "DEFAULT_CACHE_TTL_HOURS",
    "DEFAULT_PROJECT",
    "AppConfig",
    "ConfigError",
    "load_config",
]
