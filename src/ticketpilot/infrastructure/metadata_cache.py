"""24-hour metadata caching around a read-only metadata gateway."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ticketpilot.application.ports import MetadataContext
from ticketpilot.domain.models import IssueType

from .gateways import MetadataGatewayAdapter
from .persistence import SQLiteStore
from .security import safe_error_message

DEFAULT_SMALL_METADATA_KINDS = (
    "issue_types",
    "priorities",
    "components",
    "teams",
    "link_types",
    "boards",
    "sprints",
    "fix_versions",
)


@dataclass(frozen=True, slots=True)
class MetadataRefreshResult:
    kind: str
    succeeded: bool
    item_count: int = 0
    message: str = ""


class CachingMetadataProvider:
    """Cache only bounded lists and typed field contexts.

    Dynamic searches for people, epics, tickets, products/services and accounts
    deliberately bypass this class and remain on-demand gateway operations.
    A forced refresh never falls back to stale data, preserving the critical
    metadata check required immediately before a write.
    """

    def __init__(self, upstream: MetadataGatewayAdapter, store: SQLiteStore) -> None:
        self.upstream = upstream
        self.store = store

    def get_context(
        self,
        project: str,
        issue_type: IssueType,
        *,
        force_refresh: bool = False,
    ) -> MetadataContext:
        key = _context_key(project, issue_type)
        if not force_refresh:
            cached = self.store.get_metadata(key)
            if isinstance(cached, Mapping):
                return _context_from_cache(cached)
        try:
            context = self.upstream.get_context(
                project,
                issue_type,
                force_refresh=force_refresh,
            )
        except Exception:
            if force_refresh:
                raise
            stale = self.store.get_metadata(key, allow_stale=True)
            if isinstance(stale, Mapping):
                return _context_from_cache(stale)
            raise
        self.store.put_metadata(
            key,
            "field_context",
            _context_to_cache(context),
            project_key=project,
        )
        return context

    def get_metadata(
        self,
        kind: str,
        *,
        project: str = "DAH",
        force_refresh: bool = False,
    ) -> Any:
        normalized_kind = _kind(kind)
        if normalized_kind not in DEFAULT_SMALL_METADATA_KINDS:
            raise ValueError(
                f"{kind!r} is not a bounded metadata list and must be searched on demand"
            )
        key = _list_key(project, normalized_kind)
        if not force_refresh:
            cached = self.store.get_metadata(key)
            if cached is not None:
                return cached
        try:
            value = self.upstream.get_metadata(
                normalized_kind,
                project=project,
                force_refresh=force_refresh,
            )
        except Exception:
            if force_refresh:
                raise
            stale = self.store.get_metadata(key, allow_stale=True)
            if stale is not None:
                return stale
            raise
        self.store.put_metadata(
            key,
            normalized_kind,
            value,
            project_key=project,
        )
        return value

    def refresh(
        self,
        *,
        project: str = "DAH",
        kinds: Iterable[str] = DEFAULT_SMALL_METADATA_KINDS,
    ) -> tuple[MetadataRefreshResult, ...]:
        """Refresh every requested small list independently for a UI progress view."""

        results: list[MetadataRefreshResult] = []
        for kind in kinds:
            normalized = _kind(kind)
            try:
                value = self.get_metadata(normalized, project=project, force_refresh=True)
                count = len(value) if hasattr(value, "__len__") else 0
                results.append(MetadataRefreshResult(normalized, True, count))
            except Exception as error:
                results.append(
                    MetadataRefreshResult(
                        normalized,
                        False,
                        message=safe_error_message(error),
                    )
                )
        return tuple(results)


def _context_to_cache(context: MetadataContext) -> dict[str, Any]:
    return {
        "project": context.project,
        "issue_type": context.issue_type.value,
        "available_fields": (
            sorted(context.available_fields) if context.available_fields is not None else None
        ),
        "required_fields": sorted(context.required_fields),
        "field_ids": dict(context.field_ids or {}),
        "revision": context.revision,
        "checked_at": context.checked_at.isoformat() if context.checked_at else None,
    }


def _context_from_cache(raw: Mapping[str, Any]) -> MetadataContext:
    issue_type = IssueType(str(raw["issue_type"]))
    available_raw = raw.get("available_fields")
    available = (
        None
        if available_raw is None
        else frozenset(str(item) for item in _iterable(available_raw))
    )
    checked_raw = raw.get("checked_at")
    checked = datetime.fromisoformat(str(checked_raw)) if checked_raw else None
    return MetadataContext(
        project=str(raw["project"]),
        issue_type=issue_type,
        available_fields=available,
        required_fields=frozenset(str(item) for item in _iterable(raw.get("required_fields", ()))),
        field_ids={
            str(key): str(value)
            for key, value in _mapping(raw.get("field_ids", {})).items()
        },
        revision=str(raw.get("revision", "")),
        checked_at=checked,
    )


def _iterable(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return value
    raise ValueError("Cached metadata list has an invalid shape")


def _mapping(value: Any) -> Mapping[Any, Any]:
    if isinstance(value, Mapping):
        return value
    raise ValueError("Cached metadata mapping has an invalid shape")


def _context_key(project: str, issue_type: IssueType) -> str:
    return f"context:{project.strip().upper()}:{issue_type.value}"


def _list_key(project: str, kind: str) -> str:
    return f"metadata:{project.strip().upper()}:{kind}"


def _kind(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


__all__ = [
    "DEFAULT_SMALL_METADATA_KINDS",
    "CachingMetadataProvider",
    "MetadataRefreshResult",
]
