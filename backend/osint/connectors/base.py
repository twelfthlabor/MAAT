"""Base connector interface and common behavior."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from backend.core.config import settings
from backend.osint.normalization.models import ConnectorRunResult, QueryContext


@dataclass(slots=True)
class ConnectorMetadata:
    """Human-readable connector metadata."""

    name: str
    source_kind: str
    disabled_by_default: bool
    unstable: bool = False
    description: str = ""
    timeout_seconds: float | None = None


class Connector(Protocol):
    """Protocol implemented by all connectors."""

    metadata: ConnectorMetadata

    def enabled(self) -> bool:
        """Return whether the connector is enabled."""

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        """Execute the connector."""


async def rate_limit_sleep() -> None:
    """Shared rate-limit pause."""
    await asyncio.sleep(settings.connector_delay_seconds)
