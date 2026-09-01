"""Dataclasses for normalized connector results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class QueryContext:
    """Case context passed into connectors."""

    case_id: int
    name: str
    aliases: list[str]
    city: str | None
    province: str | None
    age: int | None
    missing_since: datetime | None
    location_text: str | None = None
    authority_name: str | None = None
    authority_case_url: str | None = None
    case_reference_url: str | None = None
    source_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    usernames: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NormalizedLead:
    """Connector output normalized into the platform contract."""

    connector_name: str
    source_kind: str
    lead_type: str
    category: str
    source_name: str
    source_url: str
    query_used: str
    found_at: datetime
    title: str
    summary: str
    content_excerpt: str = ""
    published_at: datetime | None = None
    location_text: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source_trust: float = 0.5
    corroboration_count: int = 1
    rationale: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConnectorRunResult:
    """Connector execution result."""

    leads: list[NormalizedLead] = field(default_factory=list)
    warning: str | None = None
    query_logs: list[dict] = field(default_factory=list)
