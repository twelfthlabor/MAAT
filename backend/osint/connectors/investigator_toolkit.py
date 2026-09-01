"""OSINT investigator toolkit connector — Trace Labs methodology.

Generates high-value analyst action links for tools and techniques
used by professional OSINT investigators and Trace Labs CTF participants.
These are the methods that actually find people.

This connector doesn't scrape — it produces actionable investigation
checklists with direct links to:
  - Username enumeration tools (Sherlock, Namechk, WhatsMyName)
  - People search engines (that work in Canada)
  - Public records and court records
  - Phone/email OSINT tools
  - Geolocation and mapping tools
  - Social media analytics
  - Archived web content tools
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext


def _build_username_variants(name: str) -> list[str]:
    """Generate likely username patterns from a full name."""
    parts = [p.lower() for p in name.split() if len(p) >= 2]
    if len(parts) < 2:
        return [name.lower().replace(" ", "")] if name else []
    first, last = parts[0], parts[-1]
    variants = [
        f"{first}{last}",           # johnsmith
        f"{first}.{last}",          # john.smith
        f"{first}_{last}",          # john_smith
        f"{first}{last[0]}",        # johns
        f"{first[0]}{last}",        # jsmith
        f"{last}{first}",           # smithjohn
        f"{first}{last}99",         # johnsmith99 (common pattern)
        f"{first}.{last}1",         # john.smith1
    ]
    return variants[:6]


class InvestigatorToolkitConnector:
    """Generate analyst action links for professional OSINT investigation tools."""

    metadata = ConnectorMetadata(
        name="investigator-toolkit",
        source_kind="clear-web",
        disabled_by_default=True,
        description=(
            "Trace Labs-style investigator toolkit — generates analyst action links "
            "for username enumeration, people search, public records, and geolocation tools."
        ),
    )

    def enabled(self) -> bool:
        return bool(settings.enable_investigator_mode)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Investigator toolkit disabled by configuration.")

        name = context.name or ""
        city = context.city or ""
        province = context.province or ""
        if not name:
            return ConnectorRunResult(warning="No subject name available for toolkit generation.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        found_at = datetime.now(timezone.utc)
        encoded_name = quote(name)
        username_variants = _build_username_variants(name)

        # ── Username Enumeration Tools ──
        for username in username_variants[:3]:
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind=self.metadata.source_kind,
                    lead_type="analyst-action",
                    category="username-enumeration",
                    source_name="Sherlock Project",
                    source_url=f"https://sherlockproject.xyz/",
                    query_used=username,
                    found_at=found_at,
                    title=f"Username enumeration — Sherlock: '{username}'",
                    summary=(
                        f"Run Sherlock to check if username '{username}' exists across 400+ social networks. "
                        f"Command: sherlock {username} --print-found"
                    ),
                    content_excerpt=f"sherlock {username} --print-found --csv",
                    source_trust=0.30,
                    rationale=[
                        "Sherlock checks 400+ sites for username existence — key Trace Labs technique.",
                        "Username variants generated from subject's name.",
                        "If a match is found, it can lead to social media profiles with additional intel.",
                    ],
                )
            )

        # WhatsMyName — web-based username search
        for username in username_variants[:2]:
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind=self.metadata.source_kind,
                    lead_type="analyst-action",
                    category="username-enumeration",
                    source_name="WhatsMyName",
                    source_url=f"https://whatsmyname.app/",
                    query_used=username,
                    found_at=found_at,
                    title=f"Username check — WhatsMyName: '{username}'",
                    summary=(
                        f"Browser-based username enumeration tool. Search for '{username}' "
                        "across hundreds of websites without installing any tools."
                    ),
                    content_excerpt=f"Search WhatsMyName for: {username}",
                    source_trust=0.30,
                    rationale=[
                        "WhatsMyName — web-based alternative to Sherlock for quick username checks.",
                        "No installation required — works in any browser.",
                    ],
                )
            )

        # ── People Search / Public Records ──
        people_search_tools = [
            {
                "name": "Canada411",
                "url": f"https://www.canada411.ca/search/?stype=si&what={encoded_name}&where={quote(city) if city else ''}",
                "summary": f"Search Canada411 for '{name}' — Canadian people/phone directory.",
                "rationale": "Canada411 — public phone/address directory specific to Canada.",
                "trust": 0.50,
            },
            {
                "name": "411.ca",
                "url": f"https://411.ca/search/?q={encoded_name}&st=People",
                "summary": f"Search 411.ca for '{name}' — Canadian people finder.",
                "rationale": "411.ca reverse lookup — may reveal phone numbers, addresses, and relatives.",
                "trust": 0.45,
            },
            {
                "name": "Google Cache / Archive Search",
                "url": f"https://webcache.googleusercontent.com/search?q=cache:{encoded_name}+missing",
                "summary": f"Search Google's cache for '{name}' — finds deleted or modified pages.",
                "rationale": "Google cache preserves deleted content — key technique for finding removed posts.",
                "trust": 0.40,
            },
            {
                "name": "Wayback Machine Search",
                "url": f"https://web.archive.org/web/*/{encoded_name}",
                "summary": f"Search the Wayback Machine for archived pages mentioning '{name}'.",
                "rationale": "Internet Archive — finds archived versions of deleted web pages and profiles.",
                "trust": 0.50,
            },
        ]

        for tool in people_search_tools:
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind=self.metadata.source_kind,
                    lead_type="analyst-action",
                    category="people-search",
                    source_name=tool["name"],
                    source_url=tool["url"],
                    query_used=name,
                    found_at=found_at,
                    title=f"People search — {tool['name']}",
                    summary=tool["summary"],
                    content_excerpt=tool["summary"],
                    location_text=city or province,
                    source_trust=tool["trust"],
                    rationale=[
                        tool["rationale"],
                        "Analyst action — open the link to perform the search.",
                    ],
                )
            )

        # ── Geolocation & Mapping Tools ──
        if city:
            geo_tools = [
                {
                    "name": "Google Maps Street View",
                    "url": f"https://www.google.com/maps/search/{quote(f'{city} {province}')}",
                    "summary": f"Street View of last-known area in {city}, {province}. Look for CCTV cameras, transit stops, shelters.",
                    "rationale": "Street View — identify CCTV cameras, bus stops, and shelters near last-seen location.",
                },
                {
                    "name": "Snap Map",
                    "url": f"https://map.snapchat.com/",
                    "summary": f"Snapchat's Snap Map — check for geotagged public Snaps near {city}.",
                    "rationale": "Snap Map shows public Snaps by location — may capture sightings near last-seen area.",
                },
            ]
            for tool in geo_tools:
                leads.append(
                    NormalizedLead(
                        connector_name=self.metadata.name,
                        source_kind=self.metadata.source_kind,
                        lead_type="analyst-action",
                        category="geolocation",
                        source_name=tool["name"],
                        source_url=tool["url"],
                        query_used=f"{city}, {province}",
                        found_at=found_at,
                        title=f"Geolocation — {tool['name']} ({city})",
                        summary=tool["summary"],
                        content_excerpt=tool["summary"],
                        location_text=f"{city}, {province}",
                        source_trust=0.35,
                        rationale=[
                            tool["rationale"],
                            "Geolocation intelligence — Trace Labs 'Last Known Location' category.",
                        ],
                    )
                )

        # ── Email Pattern Discovery ──
        if name:
            name_parts = [p.lower() for p in name.split() if len(p) >= 2]
            if len(name_parts) >= 2:
                email_patterns = [
                    f"{name_parts[0]}.{name_parts[-1]}@gmail.com",
                    f"{name_parts[0]}{name_parts[-1]}@gmail.com",
                    f"{name_parts[0]}.{name_parts[-1]}@hotmail.com",
                    f"{name_parts[0]}{name_parts[-1]}@outlook.com",
                    f"{name_parts[0]}.{name_parts[-1]}@yahoo.com",
                ]
                leads.append(
                    NormalizedLead(
                        connector_name=self.metadata.name,
                        source_kind=self.metadata.source_kind,
                        lead_type="analyst-action",
                        category="email-enumeration",
                        source_name="Email Pattern Generator",
                        source_url="https://epieos.com/",
                        query_used=name,
                        found_at=found_at,
                        title=f"Email discovery — check common patterns for {name}",
                        summary=(
                            f"Common email patterns to check with Epieos or HaveIBeenPwned: "
                            f"{', '.join(email_patterns[:3])}. "
                            "Use Epieos to check if these emails are linked to any accounts."
                        ),
                        content_excerpt="\n".join(email_patterns),
                        source_trust=0.25,
                        rationale=[
                            "Email pattern enumeration — Trace Labs technique for discovering online accounts.",
                            "Check these patterns with Epieos, HaveIBeenPwned, or Holehe.",
                            "If an email is validated, it can unlock account recovery flows and linked accounts.",
                        ],
                    )
                )

        # ── Tip Line & Report Sighting Links ──
        tip_links = [
            {
                "name": "MCSC Report Sighting",
                "url": "https://mcsc.ca/report-a-sighting/",
                "summary": f"Report a sighting of {name} to the Missing Children Society of Canada.",
                "rationale": "Official MCSC sighting report form — the primary intake for case leads.",
            },
            {
                "name": "MissingKids.ca Tip Line",
                "url": "https://missingkids.ca/en/help-us-find/tip-or-sighting/",
                "summary": f"Submit a tip or sighting to the Canadian Centre for Child Protection about {name}.",
                "rationale": "MissingKids.ca official tip line — operated by Canadian Centre for Child Protection.",
            },
            {
                "name": "Crime Stoppers Canada",
                "url": "https://www.canadiancrimestoppers.org/",
                "summary": f"Anonymous tip line — report information about {name}'s disappearance.",
                "rationale": "Crime Stoppers allows anonymous tips — important channel for sensitive information.",
            },
        ]
        for tip in tip_links:
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind="official",
                    lead_type="tip-line",
                    category="reporting-channel",
                    source_name=tip["name"],
                    source_url=tip["url"],
                    query_used=name,
                    found_at=found_at,
                    title=f"Report tip/sighting — {tip['name']}",
                    summary=tip["summary"],
                    content_excerpt=tip["summary"],
                    source_trust=0.90,
                    rationale=[
                        tip["rationale"],
                        "Always report actionable intelligence to official channels, never act independently.",
                    ],
                )
            )

        query_logs.append({
            "connector_name": self.metadata.name,
            "source_kind": self.metadata.source_kind,
            "query_used": f"toolkit generation for {name}",
            "status": "completed",
            "http_status": 200,
            "result_count": len(leads),
            "notes": (
                f"Generated {len(leads)} analyst action links: "
                f"username enumeration, people search, geolocation, email patterns, and tip lines."
            ),
        })

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
