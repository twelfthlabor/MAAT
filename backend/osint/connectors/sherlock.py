"""Sherlock connector for real public username enumeration.

Sherlock is intentionally executed as a local subprocess instead of being
reimplemented. Its matches are account candidates, not identity proof and not
location evidence; every result remains unverified until a human reviews it.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import re
import shutil
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", normalized.lower())


def build_username_candidates(context: QueryContext) -> list[str]:
    """Build a small, deterministic set of username hypotheses from known names."""

    candidates: list[str] = []
    for username in context.usernames:
        cleaned = username.strip().strip("@")
        if re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", cleaned) and cleaned not in candidates:
            candidates.append(cleaned)

    for full_name in [context.name, *context.aliases]:
        parts = [_slug(part) for part in full_name.split()]
        parts = [part for part in parts if len(part) >= 2]
        if len(parts) < 2:
            continue
        first, last = parts[0], parts[-1]
        for candidate in (
            f"{first}{last}",
            f"{first}.{last}",
            f"{first}_{last}",
            f"{first[0]}{last}",
        ):
            if 4 <= len(candidate) <= 30 and candidate not in candidates:
                candidates.append(candidate)
    return candidates[: max(1, settings.sherlock_max_usernames)]


def _resolve_command() -> list[str] | None:
    """Resolve a configured or installed Sherlock executable without a shell."""

    if settings.sherlock_binary:
        resolved = shutil.which(settings.sherlock_binary)
        if resolved:
            return [resolved]
        configured = Path(settings.sherlock_binary)
        if configured.is_file():
            return [str(configured)]

    installed = shutil.which("sherlock")
    if installed:
        return [installed]

    try:
        module_available = importlib.util.find_spec("sherlock_project.sherlock") is not None
    except ModuleNotFoundError:
        module_available = False
    if module_available:
        return [sys.executable, "-m", "sherlock_project"]
    return None


def _is_public_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def parse_sherlock_csv(path: Path, context: QueryContext) -> list[NormalizedLead]:
    """Normalize claimed-account rows from one Sherlock CSV report."""

    leads: list[NormalizedLead] = []
    if not path.is_file():
        return leads

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            status = (row.get("exists") or "").strip().lower()
            source_url = (row.get("url_user") or "").strip()
            if "claimed" not in status or not _is_public_url(source_url):
                continue
            username = (row.get("username") or path.stem).strip()
            site_name = (row.get("name") or urlsplit(source_url).netloc).strip()
            leads.append(
                NormalizedLead(
                    connector_name="sherlock",
                    source_kind="public-profile",
                    lead_type="username-account-candidate",
                    category="username-enumeration",
                    source_name=site_name,
                    source_url=source_url,
                    query_used=username,
                    found_at=datetime.now(timezone.utc),
                    title=f"Unverified @{username} account candidate on {site_name}",
                    summary=(
                        f"Sherlock reported that @{username} is claimed on {site_name}. "
                        "The username match alone does not establish identity or current location."
                    ),
                    content_excerpt=f"Public profile candidate: {source_url}",
                    location_text=None,
                    source_trust=0.25,
                    rationale=[
                        "Collected by the upstream Sherlock Project username-enumeration tool.",
                        "Username availability is an investigative pivot, not identity confirmation.",
                        "Manually compare public profile details with official case facts before escalation.",
                    ],
                )
            )
    return leads


class SherlockConnector:
    """Run the upstream Sherlock CLI against bounded username hypotheses."""

    metadata = ConnectorMetadata(
        name="sherlock",
        source_kind="public-profile",
        disabled_by_default=True,
        description="Execute Sherlock to enumerate public account candidates by username.",
        timeout_seconds=90,
    )

    def enabled(self) -> bool:
        return bool(settings.enable_public_profile_checks and _resolve_command())

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        command = _resolve_command()
        if not settings.enable_public_profile_checks:
            return ConnectorRunResult(warning="Sherlock disabled by public-profile configuration.")
        if not command:
            return ConnectorRunResult(
                warning="Sherlock is not installed. Install sherlock-project or set SHERLOCK_BINARY."
            )

        usernames = build_username_candidates(context)
        if not usernames:
            return ConnectorRunResult(warning="No bounded username hypotheses could be derived from case facts.")

        timed_out = False
        stderr_text = ""
        return_code = 1
        with tempfile.TemporaryDirectory(prefix="maat-sherlock-") as temp_dir:
            output_dir = Path(temp_dir)
            args = [
                *command,
                *usernames,
                "--folderoutput",
                str(output_dir),
                "--csv",
                "--print-found",
                "--no-color",
                "--timeout",
                str(settings.sherlock_site_timeout_seconds),
            ]
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=settings.sherlock_timeout_seconds,
                )
            except asyncio.TimeoutError:
                timed_out = True
                process.kill()
                _, stderr = await process.communicate()

            return_code = process.returncode or 0
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            leads: list[NormalizedLead] = []
            seen_urls: set[str] = set()
            for username in usernames:
                for lead in parse_sherlock_csv(output_dir / f"{username}.csv", context):
                    if lead.source_url not in seen_urls:
                        seen_urls.add(lead.source_url)
                        leads.append(lead)

        status = "timeout" if timed_out else "completed" if return_code == 0 else "failed"
        notes = f"Sherlock checked {len(usernames)} username hypotheses using its live site catalog."
        if timed_out:
            notes += " The bounded process timed out; partial CSV results were preserved."
        elif return_code != 0:
            notes += f" Process exited with code {return_code}."
        if stderr_text:
            notes += f" Diagnostic: {stderr_text[:240]}"

        warning = None
        if timed_out:
            warning = "Sherlock timed out; any returned profile candidates are partial."
        elif return_code != 0:
            warning = "Sherlock exited unsuccessfully; review the query log and executable configuration."

        return ConnectorRunResult(
            leads=leads,
            warning=warning,
            query_logs=[
                {
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": ", ".join(usernames),
                    "status": status,
                    "http_status": None,
                    "result_count": len(leads),
                    "notes": notes,
                }
            ],
        )
