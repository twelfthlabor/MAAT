"""Transparent lead scoring with rationale output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from backend.models.case import Case
from backend.osint.normalization.models import NormalizedLead
from shared.utils.dates import ensure_utc
from shared.utils.geo import haversine_km
from shared.utils.text import token_similarity


@dataclass(slots=True)
class ScoredLead:
    """Lead score details."""

    score: float
    rationale: list[str]


def _recency_score(published_at: datetime | None, missing_since: datetime | None) -> tuple[float, str | None]:
    if published_at is None:
        return 0.1, None
    pub = ensure_utc(published_at)
    if missing_since is None:
        age_days = max(0, int((datetime.now(timezone.utc) - pub).total_seconds() // 86400))
        if age_days <= 7:
            return 0.7, "Published within the last 7 days."
        if age_days <= 30:
            return 0.45, "Published within the last 30 days."
        return 0.2, "Older content lowers recency relevance."

    ms = ensure_utc(missing_since)
    delta_days = int((pub - ms).total_seconds() // 86400)
    if delta_days < 0:
        return 0.05, "Content predates the disappearance."
    if delta_days <= 7:
        return 1.0, "Content appeared within a week of the disappearance."
    if delta_days <= 30:
        return 0.7, "Content appeared within a month of the disappearance."
    if delta_days <= 180:
        return 0.4, "Content appeared months after the disappearance."
    return 0.2, "Content is temporally distant from the disappearance."


_MISSING_KEYWORDS = {
    "missing", "disappeared", "last seen", "police", "rcmp", "amber alert",
    "vulnerable", "appeal", "search", "located", "found safe", "sighting",
    "tips", "reward", "abducted", "runaway", "endangered",
    "disparition", "disparu", "disparue", "fugue", "derniere fois vue",
    "appel a temoins", "portee disparue", "porte disparu", "recherchee", "recherche",
}

_IRRELEVANT_KEYWORDS = {
    "obituary", "funeral", "rip", "condolences", "memorial",
    "sports score", "roster", "fantasy", "draft pick", "trade",
    "recipe", "cookbook", "restaurant review",
    "pornstar", "porn", "xxx", "onlyfans", "escort",
    "nylon-queens", "foxy reviews",
}

_ACTIONABLE_LOCATION_TERMS = {
    "last seen", "seen near", "seen in", "seen at", "in the area", "near the",
    "intersection", "boulevard", "avenue", "street", "highway", "station",
    "terminal", "park", "mall", "hospital", "school", "sector", "quartier",
    "derniere fois vue", "vue pres", "vue dans", "secteur", "rue", "autoroute",
}

_ACTIONABLE_DESCRIPTION_TERMS = {
    "wearing", "clothing", "hoodie", "jacket", "pants", "shoes", "bag",
    "backpack", "coat", "sweater", "hair", "eyes", "portait", "vetue",
    "cheveux", "yeux", "sac",
}

_ACTIONABLE_VEHICLE_TERMS = {
    "vehicle", "driving", "plate", "licence plate", "license plate", "car", "truck",
    "suv", "van", "toyota", "honda", "ford",
}

_ACTIONABLE_MOVEMENT_TERMS = {
    "heading", "toward", "towards", "traveling", "travelling", "direction",
    "left home", "ran away", "went missing from", "may be in", "en direction",
    "vers", "quitte", "en fugue de", "pourrait se trouver",
}

_AMPLIFICATION_TERMS = {
    "please share", "please rt", "share this", "retweet", "read & rt", "how to help",
    "join the conversation", "support us", "subscribe to our newsletter",
}


def _name_in_text(name: str, text: str) -> bool:
    """Check if the full name appears in the text (case-insensitive)."""
    return name.lower() in text.lower()


def _has_expanded_single_name(case_name: str, text: str) -> bool:
    """Detect a likely namesake when the case only has a given name."""
    parts = [part for part in case_name.split() if len(part) >= 3]
    if len(parts) != 1:
        return False
    escaped = re.escape(parts[0])
    return bool(re.search(rf"\b{escaped}\s+[a-z][a-z'-]{{2,}}\b", text, flags=re.IGNORECASE))


def _case_specific_anchor_hits(case: Case, text: str) -> list[str]:
    """Return distinctive facts from the official summary found in a result."""
    summary = re.sub(r"<[^>]+>", " ", case.official_summary_html or "").lower()
    anchors: set[str] = set()

    for pattern in (
        r"\b(?:blue|black|white|red|grey|gray)\s+(?:honda|toyota|ford|chevrolet|chevy|dodge|nissan|mazda|volkswagen)\b",
        r"\b[a-z0-9][a-z0-9'-]*\s+(?:road|street|avenue|boulevard|drive|lane|highway)\b",
        r"\b\d{1,2}['’]\d{1,2}(?:\"|″)?\b",
        r"\b(?:reference|file)(?:\s+case)?\s*(?:#|number)?\s*[a-z0-9-]{4,}\b",
    ):
        anchors.update(match.group(0).strip() for match in re.finditer(pattern, summary))

    return sorted(anchor for anchor in anchors if anchor in text)


def _relevance_score(case: Case, lead: NormalizedLead) -> tuple[float, list[str]]:
    """Score how relevant a lead is to a missing-person case vs coincidental name match."""
    text_blob = " ".join(filter(None, [
        lead.title or "", lead.summary or "", lead.content_excerpt or "",
    ])).lower()
    reasons: list[str] = []
    score = 0.0

    name_present = bool(case.name and _name_in_text(case.name, text_blob))
    name_parts = [part.lower() for part in (case.name or "").split() if len(part) >= 3]
    anchor_hits = _case_specific_anchor_hits(case, text_blob)
    expanded_single_name = bool(
        case.name and _has_expanded_single_name(case.name, text_blob)
    )

    matched_keywords = [kw for kw in _MISSING_KEYWORDS if kw in text_blob]
    if matched_keywords:
        boost = min(0.5, len(matched_keywords) * 0.15)
        score += boost
        reasons.append(f"Missing-person keywords found: {', '.join(matched_keywords[:4])}")

    repeat_missing_signals = [
        "case update", "updated photo", "still missing", "still trying to locate",
        "last seen on", "updated information", "previously reported missing",
        "continue to search", "renewed appeal",
    ]
    repeat_hits = [s for s in repeat_missing_signals if s in text_blob]
    if repeat_hits and name_present:
        score += 0.35
        reasons.append(f"REPEAT-MISSING PATTERN: historical record for same person ({', '.join(repeat_hits[:2])})")

    if case.city and case.city.lower() in text_blob:
        score += 0.2
        reasons.append("Lead mentions the case city.")
    if case.authority_name and case.authority_name.lower() in text_blob:
        score += 0.25
        reasons.append("Lead mentions the investigating authority.")
    if case.age is not None and str(case.age) in text_blob:
        age_contexts = [f"{case.age}-year", f"{case.age} year", f"age {case.age}", f"age: {case.age}"]
        if any(ctx in text_blob for ctx in age_contexts):
            score += 0.15
            reasons.append("Lead mentions the subject's age in context.")

    irrelevant_hits = [kw for kw in _IRRELEVANT_KEYWORDS if kw in text_blob]
    if irrelevant_hits:
        penalty = min(0.4, len(irrelevant_hits) * 0.15)
        score -= penalty
        reasons.append(f"Irrelevant content detected: {', '.join(irrelevant_hits[:3])}")

    if lead.published_at and case.missing_since:
        pub = ensure_utc(lead.published_at)
        ms = ensure_utc(case.missing_since)
        if pub and ms:
            delta = (pub - ms).total_seconds() / 86400
            if delta < -30:
                if matched_keywords and name_present:
                    score += 0.1
                    reasons.append("Historical missing-person record for the same individual - valuable context.")
                else:
                    score -= 0.35
                    reasons.append("Content published well before the disappearance with no missing-person context - likely a different person.")

    if not matched_keywords and not name_present and lead.source_kind != "official":
        score -= 0.2
        reasons.append("No missing-person keywords and name not found - likely irrelevant.")

    # Given names such as Joshua, Michael, or David produce many unrelated
    # search results. Require a case-specific anchor or the case geography /
    # authority before allowing those results to rank as strong evidence.
    if len(name_parts) == 1 and lead.source_kind != "official":
        has_case_geography = bool(
            (case.authority_name and case.authority_name.lower() in text_blob)
            or (
                case.city and case.city.lower() in text_blob
                and case.province and case.province.lower() in text_blob
            )
        )
        if not has_case_geography and len(anchor_hits) < 2:
            score -= 0.5
            reasons.append(
                "Ambiguous single-name result lacks case geography, authority, or two distinctive official anchors."
            )
        elif anchor_hits:
            reasons.append(f"Case-specific official anchor(s) matched: {', '.join(anchor_hits[:3])}.")
        if not name_present:
            score -= 0.35
            reasons.append("Single-name result does not contain the subject name in its returned text; retain as context only until identity is confirmed.")
        elif expanded_single_name:
            score -= 0.25
            reasons.append(
                "Result pairs the given name with another surname; treat as a possible namesake until identity is confirmed."
            )

    # Penalize leads where no part of the person's name appears at all.
    # This filters cross-case contamination (e.g., different missing persons
    # returned by broad keyword searches).
    if case.name and lead.source_kind != "official":
        parts_found = sum(1 for p in name_parts if p in text_blob)
        if parts_found == 0 and name_parts:
            score -= 0.30
            reasons.append("No part of the subject's name found in lead text — likely about a different person.")

    if lead.source_kind == "official":
        score += 0.2
        reasons.append("Official source gets a relevance boost.")

    return max(0.0, min(1.0, score)), reasons


def _actionability_delta(lead: NormalizedLead) -> tuple[float, list[str]]:
    """Estimate whether a lead adds locating detail vs only amplifying an alert."""

    text_blob = " ".join(
        filter(None, [lead.title or "", lead.summary or "", lead.content_excerpt or "", lead.location_text or ""])
    ).lower()

    delta = 0.0
    reasons: list[str] = []

    has_location_detail = any(term in text_blob for term in _ACTIONABLE_LOCATION_TERMS)
    has_description_detail = any(term in text_blob for term in _ACTIONABLE_DESCRIPTION_TERMS)
    has_vehicle_detail = any(term in text_blob for term in _ACTIONABLE_VEHICLE_TERMS)
    has_movement_detail = any(term in text_blob for term in _ACTIONABLE_MOVEMENT_TERMS)

    if lead.lead_type in {"official-last-seen", "sighting-trace"}:
        delta += 0.08
        reasons.append("Lead is typed as last-seen or sighting intelligence.")
    if has_location_detail:
        delta += 0.12
        reasons.append("Lead contains last-seen or location-specific detail.")
    if has_description_detail:
        delta += 0.08
        reasons.append("Lead contains physical or clothing detail.")
    if has_vehicle_detail:
        delta += 0.08
        reasons.append("Lead contains vehicle or plate detail.")
    if has_movement_detail:
        delta += 0.06
        reasons.append("Lead contains movement or travel-direction detail.")

    if any(term in text_blob for term in _AMPLIFICATION_TERMS) and delta < 0.08 and lead.source_kind != "official":
        delta -= 0.05
        reasons.append("Amplification/share language without new locating detail lowers utility.")

    return delta, reasons


def score_lead(case: Case, lead: NormalizedLead) -> ScoredLead:
    """Score a normalized lead and return rationale."""
    rationale = []
    total = 0.0

    relevance_component, relevance_reasons = _relevance_score(case, lead)
    total += relevance_component * 0.30
    rationale.extend(relevance_reasons)

    name_similarity = token_similarity(case.name or "", lead.title or "")
    alias_similarity = max((token_similarity(alias, lead.title or "") for alias in case.aliases), default=0.0)
    name_component = max(name_similarity, alias_similarity)
    total += name_component * 0.15
    if name_component:
        rationale.append(f"Name/alias match quality contributed {name_component:.2f}.")

    geo_component = 0.0
    text_blob = " ".join(filter(None, [lead.summary, lead.content_excerpt, lead.location_text or ""])).lower()
    if case.city and case.city.lower() in text_blob:
        geo_component = max(geo_component, 0.8)
    if case.province and case.province.lower() in text_blob:
        geo_component = max(geo_component, 0.5)
    if case.latitude is not None and case.longitude is not None and lead.latitude is not None and lead.longitude is not None:
        distance = haversine_km(case.latitude, case.longitude, lead.latitude, lead.longitude)
        if distance <= 25:
            geo_component = max(geo_component, 1.0)
            rationale.append("Lead coordinates are within 25 km of the last known location.")
        elif distance <= 100:
            geo_component = max(geo_component, 0.6)
            rationale.append("Lead coordinates are within 100 km of the last known location.")
    total += geo_component * 0.15

    age_component = 0.0
    if case.age is not None and case.age <= 12:
        age_component = 0.3
        rationale.append("Younger-child cases receive a modest urgency boost.")
    total += age_component * 0.05

    recency_component, recency_reason = _recency_score(lead.published_at, case.missing_since)
    total += recency_component * 0.15
    if recency_reason:
        rationale.append(recency_reason)

    trust_component = max(0.0, min(1.0, lead.source_trust))
    total += trust_component * 0.10
    rationale.append(f"Source credibility contributed {trust_component:.2f}.")

    corroboration_component = min(1.0, lead.corroboration_count / 3)
    total += corroboration_component * 0.10
    if lead.corroboration_count > 1:
        rationale.append("Repeated across public query variants or source adapters; independent corroboration still requires review.")

    actionability_delta, actionability_reasons = _actionability_delta(lead)
    total += actionability_delta
    rationale.extend(actionability_reasons)

    if lead.source_kind == "dark-web-capable":
        total -= 0.05
        rationale.append("Dark-web-capable indexing results are down-weighted until manually reviewed.")

    # ── Lead-type utility boosts ────────────────────────────────────
    # Certain lead types are inherently more actionable regardless of
    # keyword presence in the text.
    if lead.lead_type == "tip-line":
        total += 0.06
        rationale.append("Tip-line leads are inherently high-value reporting channels.")
    elif lead.lead_type == "analyst-action":
        # Analyst action links are tools — their value depends on category
        action_boost = {
            "people-search": 0.05,
            "username-enumeration": 0.03,
            "geolocation": 0.04,
            "reporting-channel": 0.06,
            "email-enumeration": 0.02,
            "reverse-image": 0.04,
        }.get(lead.category, 0.02)
        total += action_boost

    # ── Sighting-report boost ───────────────────────────────────────
    sighting_signals = ["spotted", "sighted", "possibly spotted", "may have been seen",
                        "reported seeing", "believed to be seen", "unconfirmed sighting"]
    text_check = " ".join(filter(None, [lead.title or "", lead.summary or "", lead.content_excerpt or ""])).lower()
    if any(sig in text_check for sig in sighting_signals):
        total += 0.10
        rationale.append("Potential sighting language detected; manual verification required.")

    # ── Content-enrichment boost ────────────────────────────────────
    # Leads that have been enriched with extracted sighting details
    # are more valuable than raw headlines.
    enrichment_signals = [
        "Machine-extracted sighting location (unverified):",
        "Sighting location extracted:",
        "Physical description:",
        "Locations mentioned:",
    ]
    lead_rationale_text = " ".join(lead.rationale)
    if any(sig in lead_rationale_text for sig in enrichment_signals):
        total += 0.08
        rationale.append("Article content was machine-extracted; details remain unverified.")

    for existing_reason in lead.rationale:
        rationale.append(existing_reason)

    score = round(max(0.0, min(1.0, total)), 3)
    return ScoredLead(score=score, rationale=rationale)
