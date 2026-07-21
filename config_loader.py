"""Load administrative YAML and personal environment settings safely."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml
from dotenv import dotenv_values


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
DEFAULT_ENV_PATH = Path(__file__).with_name(".env")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class ConfigError(ValueError):
    """Raised when project or personal configuration is invalid."""


@dataclass(frozen=True)
class AllowedProject:
    key: str
    name: str


@dataclass(frozen=True)
class SafetyConfig:
    dry_run_default: bool = True
    allow_delete: bool = False
    allow_project_change: bool = False
    allow_issue_type_change: bool = False
    allow_status_transition: bool = False


@dataclass(frozen=True)
class MetadataConfig:
    refresh_after_hours: int = 24


@dataclass(frozen=True)
class ExcelConfig:
    default_visible_columns: Tuple[str, ...]


@dataclass(frozen=True)
class AppConfig:
    jira_url: str
    jira_token: str = field(repr=False)
    project_key: str
    excel_file: str
    dry_run: bool
    allowed_projects: Tuple[AllowedProject, ...]
    safety: SafetyConfig
    metadata: MetadataConfig
    excel: ExcelConfig
    config_path: Path

    @property
    def allowed_project_keys(self) -> Tuple[str, ...]:
        return tuple(project.key for project in self.allowed_projects)

    def validate_project(self, project_key: str) -> str:
        normalized = _normalize_project_key(project_key)
        if normalized not in self.allowed_project_keys:
            allowed = ", ".join(self.allowed_project_keys)
            raise ConfigError(
                f"Projekt '{normalized or project_key}' ist nicht freigegeben. "
                f"Erlaubt: {allowed}."
            )
        return normalized

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Expose the keys used by the existing command-line entry points."""
        return {
            "JIRA_URL": self.jira_url,
            "JIRA_TOKEN": self.jira_token,
            "PROJECT_KEY": self.project_key,
            "EXCEL_FILE": self.excel_file,
            "DRY_RUN": str(self.dry_run).lower(),
            "ALLOWED_PROJECTS": self.allowed_project_keys,
            "APP_CONFIG": self,
        }


def _normalize_project_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _require_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"{location} muss ein YAML-Objekt sein.")
    return value


def _read_yaml(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Konfigurationsdatei nicht gefunden: {path}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = (
            f" in Zeile {mark.line + 1}, Spalte {mark.column + 1}"
            if mark is not None
            else ""
        )
        raise ConfigError(f"Ungueltiges YAML in {path}{location}.") from error
    except OSError as error:
        raise ConfigError(f"Konfigurationsdatei kann nicht gelesen werden: {path}") from error
    return _require_mapping(loaded, "config.yaml")


def _read_bool(section: Mapping[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"safety.{key} muss true oder false sein.")
    return value


def _parse_allowed_projects(jira: Mapping[str, Any]) -> Tuple[AllowedProject, ...]:
    raw_projects = jira.get("allowed_projects")
    if not isinstance(raw_projects, list) or not raw_projects:
        raise ConfigError("jira.allowed_projects muss mindestens ein Projekt enthalten.")

    projects = []
    seen = set()
    for index, raw_project in enumerate(raw_projects, start=1):
        project = _require_mapping(raw_project, f"jira.allowed_projects[{index}]")
        key = _normalize_project_key(project.get("key"))
        name = str(project.get("name") or "").strip()
        if not key or not name:
            raise ConfigError(
                f"jira.allowed_projects[{index}] benoetigt key und name."
            )
        if key in seen:
            raise ConfigError(f"Projekt '{key}' ist in allowed_projects doppelt enthalten.")
        seen.add(key)
        projects.append(AllowedProject(key=key, name=name))
    return tuple(projects)


def _parse_safety(raw: Mapping[str, Any]) -> SafetyConfig:
    section = _require_mapping(raw.get("safety"), "safety")
    safety = SafetyConfig(
        dry_run_default=_read_bool(section, "dry_run_default", True),
        allow_delete=_read_bool(section, "allow_delete", False),
        allow_project_change=_read_bool(section, "allow_project_change", False),
        allow_issue_type_change=_read_bool(
            section, "allow_issue_type_change", False
        ),
        allow_status_transition=_read_bool(
            section, "allow_status_transition", False
        ),
    )
    if not safety.dry_run_default:
        raise ConfigError("safety.dry_run_default muss in Version 1 true bleiben.")
    forbidden = {
        "allow_delete": safety.allow_delete,
        "allow_project_change": safety.allow_project_change,
        "allow_issue_type_change": safety.allow_issue_type_change,
        "allow_status_transition": safety.allow_status_transition,
    }
    enabled = [name for name, value in forbidden.items() if value]
    if enabled:
        raise ConfigError(
            "Diese Sicherheitsoptionen muessen in Version 1 false bleiben: "
            + ", ".join(enabled)
        )
    return safety


def _parse_metadata(raw: Mapping[str, Any]) -> MetadataConfig:
    section = _require_mapping(raw.get("metadata"), "metadata")
    refresh = section.get("refresh_after_hours", 24)
    if isinstance(refresh, bool) or not isinstance(refresh, int) or refresh <= 0:
        raise ConfigError("metadata.refresh_after_hours muss eine positive Ganzzahl sein.")
    return MetadataConfig(refresh_after_hours=refresh)


def _parse_excel(raw: Mapping[str, Any]) -> ExcelConfig:
    section = _require_mapping(raw.get("excel"), "excel")
    columns = section.get("default_visible_columns")
    if not isinstance(columns, list) or not columns:
        raise ConfigError("excel.default_visible_columns muss eine Liste enthalten.")
    normalized = tuple(str(column).strip() for column in columns)
    if any(not column for column in normalized):
        raise ConfigError("excel.default_visible_columns enthaelt einen leeren Wert.")
    if len(set(normalized)) != len(normalized):
        raise ConfigError("excel.default_visible_columns enthaelt Duplikate.")
    return ExcelConfig(default_visible_columns=normalized)


def _load_environment(
    env_path: Optional[Path], environ: Optional[Mapping[str, str]]
) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if env_path is not None and env_path.is_file():
        for key, value in dotenv_values(env_path).items():
            if value is not None:
                values[str(key)] = str(value)

    source = os.environ if environ is None else environ
    for key, value in source.items():
        if value is not None:
            values[str(key)] = str(value)
    return values


def _parse_env_bool(value: Optional[str], default: bool, variable: str) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(
        f"{variable} muss true/false, yes/no, on/off oder 1/0 sein."
    )


def load_config(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    env_path: Optional[Path | str] = DEFAULT_ENV_PATH,
    environ: Optional[Mapping[str, str]] = None,
    require_credentials: bool = True,
) -> AppConfig:
    """Load config.yaml and overlay personal values from .env/environment."""
    resolved_config_path = Path(config_path)
    resolved_env_path = Path(env_path) if env_path is not None else None
    raw = _read_yaml(resolved_config_path)
    jira = _require_mapping(raw.get("jira"), "jira")
    allowed_projects = _parse_allowed_projects(jira)
    safety = _parse_safety(raw)
    metadata = _parse_metadata(raw)
    excel = _parse_excel(raw)
    environment = _load_environment(resolved_env_path, environ)

    legacy_project = _normalize_project_key(environment.get("PROJECT_KEY"))
    if legacy_project:
        project_key = legacy_project
    elif len(allowed_projects) == 1:
        project_key = allowed_projects[0].key
    else:
        raise ConfigError(
            "PROJECT_KEY fehlt; bei mehreren erlaubten Projekten ist eine Auswahl noetig."
        )

    allowed_keys = tuple(project.key for project in allowed_projects)
    if project_key not in allowed_keys:
        raise ConfigError(
            f"Projekt '{project_key}' ist nicht freigegeben. "
            f"Erlaubt: {', '.join(allowed_keys)}."
        )

    jira_url = environment.get("JIRA_URL", "").strip().rstrip("/")
    jira_token = environment.get("JIRA_TOKEN", "").strip()
    if require_credentials:
        missing = []
        if not jira_url:
            missing.append("JIRA_URL")
        if not jira_token:
            missing.append("JIRA_TOKEN")
        if missing:
            raise ConfigError(f"Fehlende persoenliche Konfiguration: {', '.join(missing)}")

    dry_run = _parse_env_bool(
        environment.get("DRY_RUN"), safety.dry_run_default, "DRY_RUN"
    )
    excel_file = environment.get("EXCEL_FILE", "tickets.xlsm").strip()
    if not excel_file:
        raise ConfigError("EXCEL_FILE darf nicht leer sein.")

    return AppConfig(
        jira_url=jira_url,
        jira_token=jira_token,
        project_key=project_key,
        excel_file=excel_file,
        dry_run=dry_run,
        allowed_projects=allowed_projects,
        safety=safety,
        metadata=metadata,
        excel=excel,
        config_path=resolved_config_path,
    )


def load_legacy_config(**kwargs: Any) -> Dict[str, Any]:
    """Load configuration using the dictionary shape expected by legacy code."""
    return load_config(**kwargs).to_legacy_dict()
