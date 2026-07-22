"""Read-only metadata refresh orchestration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from ticketpilot.domain.models import IssueType

from .errors import safe_error_message
from .ports import MetadataCatalogProvider, MetadataContext, MetadataContextProvider

SMALL_METADATA_KINDS = frozenset(
    {
        "issue_types",
        "priorities",
        "components",
        "teams",
        "link_types",
        "boards",
        "sprints",
        "fix_versions",
    }
)
DYNAMIC_SEARCH_RESOURCES = frozenset({"people", "epics", "issues", "products_services", "accounts"})


@dataclass(frozen=True, slots=True)
class MetadataRefreshResult:
    project: str
    issue_type: IssueType
    succeeded: bool
    context: MetadataContext | None = None
    message: str = ""


class MetadataRefreshService:
    def __init__(self, metadata: MetadataContextProvider) -> None:
        self._metadata = metadata

    def refresh(
        self,
        project: str,
        issue_types: Iterable[IssueType] = tuple(IssueType),
    ) -> tuple[MetadataRefreshResult, ...]:
        """Refresh every issue-type context independently and read-only."""

        results: list[MetadataRefreshResult] = []
        for issue_type in issue_types:
            try:
                context = self._metadata.get_context(
                    project,
                    issue_type,
                    force_refresh=True,
                )
                results.append(
                    MetadataRefreshResult(
                        project=project,
                        issue_type=issue_type,
                        succeeded=True,
                        context=context,
                        message="Metadata refreshed.",
                    )
                )
            except Exception as error:
                results.append(
                    MetadataRefreshResult(
                        project=project,
                        issue_type=issue_type,
                        succeeded=False,
                        message=safe_error_message(error),
                    )
                )
        return tuple(results)


@dataclass(frozen=True, slots=True)
class MetadataOption:
    option_id: str
    value: str
    label: str
    attributes: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class SearchPage:
    resource: str
    query: str
    start_at: int
    limit: int
    items: tuple[MetadataOption, ...]


class MetadataCatalogService:
    """Safe read-only façade for small catalogues and on-demand searches."""

    def __init__(self, metadata: MetadataCatalogProvider) -> None:
        self._metadata = metadata

    def catalogue(
        self,
        kind: str,
        *,
        project: str,
        force_refresh: bool = False,
    ) -> tuple[MetadataOption, ...]:
        normalized = kind.strip().casefold()
        if normalized not in SMALL_METADATA_KINDS:
            raise ValueError(f"Unsupported cacheable metadata kind: {kind}")
        raw = self._metadata.get_metadata(
            normalized,
            project=project,
            force_refresh=force_refresh,
        )
        return tuple(_option(item) for item in raw)

    def search(
        self,
        resource: str,
        query: str,
        *,
        project: str,
        start_at: int = 0,
        limit: int = 20,
    ) -> SearchPage:
        normalized = resource.strip().casefold()
        if normalized not in DYNAMIC_SEARCH_RESOURCES:
            raise ValueError(f"Unsupported dynamic search resource: {resource}")
        if start_at < 0 or not 1 <= limit <= 100:
            raise ValueError("Search pagination is invalid")
        raw = self._metadata.search(
            normalized,
            query,
            project=project,
            start_at=start_at,
            limit=limit,
        )
        return SearchPage(
            resource=normalized,
            query=query,
            start_at=start_at,
            limit=limit,
            items=tuple(_option(item) for item in raw),
        )


def _option(raw: Mapping[str, Any]) -> MetadataOption:
    option_id = str(raw.get("id", "")).strip()
    value = str(raw.get("value", option_id)).strip()
    label = str(raw.get("label", value)).strip()
    if not option_id or not label:
        raise ValueError("Metadata option must contain a resolved ID and label")
    attributes = {
        str(key): value for key, value in raw.items() if key not in {"id", "value", "label"}
    }
    return MetadataOption(option_id, value, label, MappingProxyType(attributes))
