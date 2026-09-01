"""MAAT Intelligence Synthesis — truth from chaos.

Clusters scored leads, detects geographic/temporal patterns,
generates actionable intelligence summaries and triage recommendations.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.osint.lead_analysis import assess_lead
from backend.osint.location_evidence import evaluate_location_evidence
from shared.utils.geo import haversine_km
from shared.utils.text import token_similarity


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LeadCluster:
    """A group of semantically or geographically related leads."""

    cluster_id: str
    label: str
    theme: str
    lead_ids: list[int] = field(default_factory=list)
    avg_confidence: float = 0.0
    max_confidence: float = 0.0
    source_count: int = 0
    unique_sources: list[str] = field(default_factory=list)
    location_text: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    date_range_start: str | None = None
    date_range_end: str | None = None
    summary: str = ""


@dataclass(slots=True)
class TimelineEvent:
    """A timeline entry with full source attribution."""

    date: str
    label: str
    kind: str  # official, news, sighting, archive, derived
    source_name: str | None = None
    source_url: str | None = None
    connector_name: str | None = None
    confidence: float | None = None
    lead_id: int | None = None


@dataclass(slots=True)
class ActionableRecommendation:
    """A concrete next-step recommendation for investigators."""

    priority: int  # 1=critical, 2=high, 3=medium
    action: str
    rationale: str
    related_cluster: str | None = None
    contact_info: str | None = None


@dataclass(slots=True)
class SynthesisReport:
    """Full intelligence synthesis output for a case investigation."""

    case_id: int
    generated_at: str
    total_leads: int
    total_clusters: int
    high_confidence_leads: int

    situation_summary: str
    key_findings: list[str]
    clusters: list[LeadCluster]
    timeline: list[TimelineEvent]
    recommendations: list[ActionableRecommendation]
    geographic_patterns: list[dict]
    temporal_patterns: list[dict]
    authority_brief: str


# ---------------------------------------------------------------------------
# Clustering by semantic similarity
# ---------------------------------------------------------------------------

def _text_key(lead: dict) -> str:
    """Build a text blob for similarity matching."""
    return " ".join(filter(None, [
        lead.get("title", ""),
        lead.get("summary", ""),
        lead.get("location_text", ""),
    ])).strip().lower()


def _analysis(lead: dict) -> dict:
    """Return supplied evidence semantics or derive them for legacy callers."""

    return lead.get("analysis") or assess_lead(lead)


def _cluster_leads(leads: list[dict]) -> list[LeadCluster]:
    """Group leads into thematic clusters based on text similarity and geography."""
    if not leads:
        return []

    clusters: list[LeadCluster] = []
    assigned: set[int] = set()

    # Sort by confidence descending — seed clusters from highest-confidence leads
    sorted_leads = sorted(leads, key=lambda x: x.get("confidence", 0), reverse=True)

    # Category-based pre-clustering
    category_groups: dict[str, list[dict]] = defaultdict(list)
    for lead in sorted_leads:
        cat = lead.get("category", "uncategorized")
        category_groups[cat].append(lead)

    cluster_idx = 0
    for category, cat_leads in category_groups.items():
        # Within each category, cluster by text similarity
        sub_clusters: list[list[dict]] = []

        for lead in cat_leads:
            lead_id = lead.get("id", 0)
            if lead_id in assigned:
                continue

            text = _text_key(lead)
            matched_cluster = None

            for sc in sub_clusters:
                anchor_text = _text_key(sc[0])
                sim = token_similarity(text, anchor_text)
                if sim >= 0.35:
                    matched_cluster = sc
                    break

                # Geographic proximity clustering
                if (lead.get("latitude") and sc[0].get("latitude") and
                        lead.get("longitude") and sc[0].get("longitude")):
                    dist = haversine_km(
                        lead["latitude"], lead["longitude"],
                        sc[0]["latitude"], sc[0]["longitude"],
                    )
                    if dist < 50:  # within 50km
                        matched_cluster = sc
                        break

            if matched_cluster is not None:
                matched_cluster.append(lead)
            else:
                sub_clusters.append([lead])

            assigned.add(lead_id)

        for group in sub_clusters:
            if not group:
                continue
            cluster_idx += 1

            confidences = [l.get("confidence", 0) for l in group]
            sources = list({l.get("source_name", "unknown") for l in group})
            dates = sorted([
                l["published_at"] for l in group
                if l.get("published_at")
            ])

            # Pick best location from cluster
            best_loc = next(
                (l for l in sorted(group, key=lambda x: x.get("confidence", 0), reverse=True)
                 if l.get("location_text")
                 and _analysis(l).get("evidence_type") != "research_tool"),
                None,
            )

            theme = _infer_theme(group, category)

            cluster = LeadCluster(
                cluster_id=f"C{cluster_idx:03d}",
                label=_cluster_label(group, theme),
                theme=theme,
                lead_ids=[l.get("id", 0) for l in group],
                avg_confidence=round(sum(confidences) / len(confidences), 3) if confidences else 0,
                max_confidence=max(confidences) if confidences else 0,
                source_count=len(sources),
                unique_sources=sources,
                location_text=best_loc.get("location_text") if best_loc else None,
                latitude=best_loc.get("latitude") if best_loc else None,
                longitude=best_loc.get("longitude") if best_loc else None,
                date_range_start=dates[0] if dates else None,
                date_range_end=dates[-1] if dates else None,
                summary=_cluster_summary(group, theme),
            )
            clusters.append(cluster)

    clusters.sort(key=lambda c: c.max_confidence, reverse=True)
    return clusters


def _infer_theme(leads: list[dict], category: str) -> str:
    """Infer the theme of a cluster from its content."""
    text_blob = " ".join(_text_key(l) for l in leads).lower()

    # "Last seen" describes the official baseline, not a new sighting. Only
    # explicit current-sighting language should create an urgent sighting
    # cluster; otherwise the report falsely escalates the original case fact.
    sighting_evidence = any(
        lead.get("lead_type") == "sighting-trace"
        or lead.get("analysis", {}).get("evidence_type") == "reported_sighting"
        or any(
            marker in " ".join(str(item) for item in lead.get("rationale", [])).lower()
            for marker in (
                "machine-extracted sighting location",
                "sighting location extracted",
                "title sighting location",
            )
        )
        or any(marker in " ".join(
            str(lead.get(key, "") or "") for key in ("title", "summary", "content_excerpt")
        ).lower() for marker in (
            "reported seeing", "possibly seen", "may have been seen",
            "believed to be seen", "unconfirmed sighting",
        ))
        for lead in leads
    )
    if sighting_evidence:
        return "potential-sighting"
    if category in {"official-last-seen", "official-anchor", "official-cross-check"}:
        return "official-update"
    if any(kw in text_blob for kw in ("rcmp", "police", "investigation", "bulletin", "amber alert")):
        return "official-update"
    if any(kw in text_blob for kw in ("news", "report", "media", "cbc", "ctv", "global")):
        return "media-coverage"
    if any(kw in text_blob for kw in ("social media", "facebook", "reddit", "twitter")):
        return "social-signal"
    if any(kw in text_blob for kw in ("archive", "wayback", "historical")):
        return "historical-record"
    if any(kw in text_blob for kw in ("reward", "tip", "contact")):
        return "tip-solicitation"

    return category or "general-intelligence"


def _cluster_label(leads: list[dict], theme: str) -> str:
    """Generate a human-readable label for a cluster."""
    theme_labels = {
        "potential-sighting": "Potential Sighting Reports",
        "official-update": "Official Law Enforcement Updates",
        "media-coverage": "News Media Coverage",
        "social-signal": "Social Media Activity",
        "historical-record": "Historical Records",
        "tip-solicitation": "Tip/Reward Information",
        "general-intelligence": "General Intelligence",
    }
    base = theme_labels.get(theme, theme.replace("-", " ").title())
    if len(leads) > 1:
        loc = next((l.get("location_text") for l in leads if l.get("location_text")), None)
        if loc:
            return f"{base} — {loc}"
    return base


def _cluster_summary(leads: list[dict], theme: str) -> str:
    """Generate a summary paragraph for a cluster."""
    count = len(leads)
    sources = list({l.get("source_name", "unknown") for l in leads})
    max_conf = max((l.get("confidence", 0) for l in leads), default=0)

    parts = [f"{count} lead{'s' if count != 1 else ''} from {len(sources)} source{'s' if len(sources) != 1 else ''}"]
    parts.append(f"peak confidence {max_conf:.0%}")

    if theme == "potential-sighting":
        parts.append("— requires immediate investigator attention")
    elif theme == "official-update":
        parts.append("— verified official channels")
    elif theme == "media-coverage":
        parts.append("— public awareness indicators")

    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Timeline construction from leads
# ---------------------------------------------------------------------------

def _build_lead_timeline(
    leads: list[dict],
    missing_since: datetime | None,
    updated_at: datetime | None,
) -> list[TimelineEvent]:
    """Build a rich timeline from official dates AND lead data."""
    events: list[TimelineEvent] = []

    # Official anchors
    if missing_since:
        ms = missing_since if missing_since.tzinfo else missing_since.replace(tzinfo=timezone.utc)
        events.append(TimelineEvent(
            date=ms.isoformat(),
            label="Official disappearance date",
            kind="official",
            source_name="Missing Children Society of Canada",
        ))

    if updated_at:
        ua = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
        events.append(TimelineEvent(
            date=ua.isoformat(),
            label="Latest official case update",
            kind="official",
            source_name="MCSC / Investigating Authority",
        ))

    # Lead-derived events
    seen_urls: set[str] = set()
    for lead in sorted(leads, key=lambda x: x.get("published_at") or "9999"):
        pub = lead.get("published_at")
        if not pub:
            continue
        url = lead.get("source_url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Only include leads with reasonable confidence
        conf = lead.get("confidence", 0)
        if conf < 0.3:
            continue

        kind = "news"
        cat = (lead.get("category") or "").lower()
        source_kind = (lead.get("source_kind") or "").lower()
        title_lower = (lead.get("title") or "").lower()

        if "official" in cat or "police" in cat or "rcmp" in title_lower:
            kind = "official"
        elif "sighting" in title_lower or "seen" in title_lower:
            kind = "sighting"
        elif "archive" in source_kind or "wayback" in source_kind:
            kind = "archive"
        elif "social" in cat or "reddit" in source_kind:
            kind = "social"

        events.append(TimelineEvent(
            date=pub,
            label=lead.get("title") or "Lead activity",
            kind=kind,
            source_name=lead.get("source_name"),
            source_url=lead.get("source_url"),
            connector_name=lead.get("source_kind"),
            confidence=conf,
            lead_id=lead.get("id"),
        ))

    events.sort(key=lambda e: e.date)
    return events


# ---------------------------------------------------------------------------
# Geographic pattern detection
# ---------------------------------------------------------------------------

def _detect_geographic_patterns(
    leads: list[dict],
    case_lat: float | None,
    case_lon: float | None,
) -> list[dict]:
    """Detect geographic clusters and movement patterns."""
    report = evaluate_location_evidence(leads)
    best = report.get("best_candidate")
    if not report["sufficient"] or not best:
        return []

    distance_from_case = None
    if (
        case_lat is not None and case_lon is not None
        and best.get("latitude") is not None and best.get("longitude") is not None
    ):
        distance_from_case = haversine_km(
            case_lat, case_lon, best["latitude"], best["longitude"],
        )

    return [{
        "type": "corroborated-location",
        "label": (
            f"{best['independent_source_count']} independent sources converge near "
            f"{best.get('location') or 'the reported coordinates'}"
        ),
        "lead_count": best["lead_count"],
        "independent_source_count": best["independent_source_count"],
        "reviewed_source_count": best["reviewed_source_count"],
        "locations": [best["location"]] if best.get("location") else [],
        "center_lat": best.get("latitude"),
        "center_lon": best.get("longitude"),
        "distance_from_case_km": distance_from_case,
        "significance": report["confidence"],
    }]


# ---------------------------------------------------------------------------
# Temporal pattern detection
# ---------------------------------------------------------------------------

def _detect_temporal_patterns(leads: list[dict], missing_since: datetime | None) -> list[dict]:
    """Detect temporal patterns in lead activity."""
    patterns: list[dict] = []
    dated = [l for l in leads if l.get("published_at")]

    # Historical name matches are useful in the evidence table, but must not
    # create a false post-disappearance activity burst or active-trail signal.
    if missing_since is not None:
        baseline = missing_since if missing_since.tzinfo else missing_since.replace(tzinfo=timezone.utc)
        current_dated = []
        for lead in dated:
            try:
                value = lead["published_at"]
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed >= baseline:
                    current_dated.append(lead)
            except (AttributeError, TypeError, ValueError):
                current_dated.append(lead)
        dated = current_dated

    if not dated:
        return patterns

    # Sort by date
    dated.sort(key=lambda x: x["published_at"])

    # Detect activity bursts (3+ leads within 7 days)
    for i, lead in enumerate(dated):
        try:
            lead_dt = datetime.fromisoformat(lead["published_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        window = [lead]
        for j in range(i + 1, len(dated)):
            try:
                other_dt = datetime.fromisoformat(dated[j]["published_at"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if (other_dt - lead_dt).days <= 7:
                window.append(dated[j])
            else:
                break

        if len(window) >= 3:
            patterns.append({
                "type": "activity-burst",
                "label": f"{len(window)} leads within 7 days starting {lead['published_at'][:10]}",
                "lead_count": len(window),
                "start_date": lead["published_at"],
                "significance": "high" if len(window) >= 5 else "medium",
            })
            break  # Report the biggest burst

    # Detect recent activity (anything in last 30 days)
    now = datetime.now(timezone.utc)
    recent = []
    for lead in dated:
        try:
            lead_dt = datetime.fromisoformat(lead["published_at"].replace("Z", "+00:00"))
            if (now - lead_dt).days <= 30:
                recent.append(lead)
        except (ValueError, TypeError):
            continue

    if recent:
        patterns.append({
            "type": "recent-activity",
            "label": f"{len(recent)} lead{'s' if len(recent) != 1 else ''} in the last 30 days — case has active public trail",
            "lead_count": len(recent),
            "significance": "high",
        })
    else:
        # Check for cold trail
        if dated:
            try:
                latest_dt = datetime.fromisoformat(dated[-1]["published_at"].replace("Z", "+00:00"))
                cold_days = (now - latest_dt).days
                if cold_days > 365:
                    patterns.append({
                        "type": "cold-trail",
                        "label": f"No public lead activity in {cold_days} days — trail is cold, renewed outreach recommended",
                        "days_since_last": cold_days,
                        "significance": "high",
                    })
            except (ValueError, TypeError):
                pass

    return patterns


# ---------------------------------------------------------------------------
# Recommendation generation
# ---------------------------------------------------------------------------

def _generate_recommendations(
    clusters: list[LeadCluster],
    geo_patterns: list[dict],
    temporal_patterns: list[dict],
    leads: list[dict],
    authority_name: str | None,
    authority_phone: str | None,
) -> list[ActionableRecommendation]:
    """Generate prioritized actionable recommendations."""
    recs: list[ActionableRecommendation] = []

    # High-confidence leads needing escalation
    high_conf = [l for l in leads if l.get("confidence", 0) >= 0.6 and l.get("review_status") == "unreviewed"]
    if high_conf:
        contact = f"Contact {authority_name}" if authority_name else "Contact the investigating authority"
        if authority_phone:
            contact += f" at {authority_phone}"
        recs.append(ActionableRecommendation(
            priority=1,
            action=f"Review and escalate {len(high_conf)} high-confidence unreviewed lead{'s' if len(high_conf) != 1 else ''}",
            rationale=f"These leads score above 60% confidence and have not been triaged. "
                      f"Top lead: \"{high_conf[0].get('title', 'Untitled')}\" at {high_conf[0].get('confidence', 0):.0%}.",
            contact_info=contact,
        ))

    # Sighting clusters
    sighting_clusters = [
        c for c in clusters
        if c.theme == "potential-sighting" and c.max_confidence >= 0.6
    ]
    for cluster in sighting_clusters:
        recs.append(ActionableRecommendation(
            priority=1,
            action=f"Investigate sighting cluster: {cluster.label}",
            rationale=f"{len(cluster.lead_ids)} potential sighting report{'s' if len(cluster.lead_ids) != 1 else ''} "
                      f"from {cluster.source_count} source{'s' if cluster.source_count != 1 else ''}. "
                      f"Peak confidence {cluster.max_confidence:.0%}.",
            related_cluster=cluster.cluster_id,
        ))

    # Geographic dispersal alert
    for pattern in geo_patterns:
        if pattern["type"] == "wide-dispersal":
            recs.append(ActionableRecommendation(
                priority=2,
                action="Consider cross-regional investigation coordination",
                rationale=pattern["label"],
            ))

    # Activity bursts
    for pattern in temporal_patterns:
        if pattern["type"] == "activity-burst":
            recs.append(ActionableRecommendation(
                priority=2,
                action=f"Investigate media attention burst: {pattern['label']}",
                rationale="Sudden bursts of public attention may coincide with new developments or tips.",
            ))
        elif pattern["type"] == "cold-trail":
            recs.append(ActionableRecommendation(
                priority=2,
                action="Initiate renewed public awareness campaign",
                rationale=pattern["label"],
            ))

    # Corroborated leads
    corroborated = [l for l in leads if l.get("corroboration_count", 0) >= 2]
    if corroborated:
        recs.append(ActionableRecommendation(
            priority=2,
            action=f"Prioritize {len(corroborated)} cross-corroborated lead{'s' if len(corroborated) != 1 else ''}",
            rationale="Leads confirmed by multiple independent connectors are more likely actionable.",
        ))

    # Default: always recommend authority notification
    if not any(r.priority == 1 for r in recs) and leads:
        contact = f"Contact {authority_name}" if authority_name else "Contact the investigating authority"
        if authority_phone:
            contact += f" at {authority_phone}"
        recs.append(ActionableRecommendation(
            priority=3,
            action="Forward all high-confidence findings to the investigating authority",
            rationale="Even without urgent sighting reports, the cumulative intelligence should be shared.",
            contact_info=contact,
        ))

    recs.sort(key=lambda r: r.priority)
    return recs


# ---------------------------------------------------------------------------
# Situation summary generation
# ---------------------------------------------------------------------------

def _build_situation_summary(
    case_name: str,
    total_leads: int,
    high_confidence: int,
    clusters: list[LeadCluster],
    geo_patterns: list[dict],
    temporal_patterns: list[dict],
) -> str:
    """Generate a human-readable situation summary."""
    parts = [f"MAAT intelligence synthesis for {case_name}: {total_leads} leads analyzed across {len(clusters)} thematic cluster{'s' if len(clusters) != 1 else ''}."]

    if high_confidence:
        parts.append(f"{high_confidence} lead{'s' if high_confidence != 1 else ''} scored above 60% confidence.")

    sighting_clusters = [
        c for c in clusters
        if c.theme == "potential-sighting" and c.max_confidence >= 0.6
    ]
    if sighting_clusters:
        parts.append(f"ALERT: {len(sighting_clusters)} potential sighting cluster{'s' if len(sighting_clusters) != 1 else ''} detected.")

    recent = [p for p in temporal_patterns if p["type"] == "recent-activity"]
    if recent:
        parts.append(f"Active trail: {recent[0]['lead_count']} leads in the last 30 days.")

    cold = [p for p in temporal_patterns if p["type"] == "cold-trail"]
    if cold:
        parts.append(f"WARNING: Trail is cold — {cold[0]['days_since_last']} days since last public activity.")

    for gp in geo_patterns:
        if gp["type"] in ("wide-dispersal", "geographic-cluster"):
            parts.append(gp["label"] + ".")

    return " ".join(parts)


def _build_authority_brief(
    case_name: str,
    authority_name: str | None,
    recommendations: list[ActionableRecommendation],
    high_confidence: int,
    clusters: list[LeadCluster],
) -> str:
    """Generate a brief suitable for forwarding to investigating authority."""
    lines = [
        f"MAAT OSINT Intelligence Brief — {case_name}",
        f"Prepared for: {authority_name or 'Investigating Authority'}",
        "",
        "KEY FINDINGS:",
    ]

    critical_recs = [r for r in recommendations if r.priority == 1]
    for rec in critical_recs:
        lines.append(f"  [CRITICAL] {rec.action}")
        lines.append(f"    → {rec.rationale}")

    if high_confidence:
        lines.append(f"  {high_confidence} high-confidence leads require review.")

    sighting_clusters = [
        c for c in clusters
        if c.theme == "potential-sighting" and c.max_confidence >= 0.6
    ]
    if sighting_clusters:
        for sc in sighting_clusters:
            lines.append(f"  Sighting cluster: {sc.label} ({len(sc.lead_ids)} reports)")

    lines.append("")
    lines.append("All leads are derived from public, lawful sources. This brief is for investigative awareness only.")
    lines.append("No contact has been made with subjects, relatives, or witnesses.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def synthesize_investigation(
    case_id: int,
    case_name: str,
    leads: list[dict],
    missing_since: datetime | None = None,
    updated_at: datetime | None = None,
    case_lat: float | None = None,
    case_lon: float | None = None,
    authority_name: str | None = None,
    authority_phone: str | None = None,
) -> SynthesisReport:
    """Run full intelligence synthesis on scored leads.

    Parameters
    ----------
    case_id : int
    case_name : str
    leads : list[dict]
        Serialized Lead dicts as returned by the run-leads endpoint.
    missing_since, updated_at : optional datetime anchors.
    case_lat, case_lon : case origin coordinates.
    authority_name, authority_phone : for recommendation routing.

    Returns
    -------
    SynthesisReport
    """
    high_confidence = [l for l in leads if l.get("confidence", 0) >= 0.6]
    clusters = _cluster_leads(leads)
    timeline = _build_lead_timeline(leads, missing_since, updated_at)
    geo_patterns = _detect_geographic_patterns(leads, case_lat, case_lon)
    temporal_patterns = _detect_temporal_patterns(leads, missing_since)

    recommendations = _generate_recommendations(
        clusters, geo_patterns, temporal_patterns, leads,
        authority_name, authority_phone,
    )

    situation_summary = _build_situation_summary(
        case_name, len(leads), len(high_confidence),
        clusters, geo_patterns, temporal_patterns,
    )

    key_findings = []
    for cluster in clusters[:5]:
        key_findings.append(f"{cluster.label}: {cluster.summary}")
    for gp in geo_patterns[:3]:
        key_findings.append(gp["label"])
    for tp in temporal_patterns[:3]:
        key_findings.append(tp["label"])

    authority_brief = _build_authority_brief(
        case_name, authority_name, recommendations,
        len(high_confidence), clusters,
    )

    return SynthesisReport(
        case_id=case_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_leads=len(leads),
        total_clusters=len(clusters),
        high_confidence_leads=len(high_confidence),
        situation_summary=situation_summary,
        key_findings=key_findings,
        clusters=clusters,
        timeline=timeline,
        recommendations=recommendations,
        geographic_patterns=geo_patterns,
        temporal_patterns=temporal_patterns,
        authority_brief=authority_brief,
    )
