"""Derive locating value and verification state from a persisted lead.

The connector confidence score answers "is this result about the case?". It does
not answer "does this result help locate the person?". This module keeps those
questions separate and can operate on ORM objects or serialized lead mappings.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse


_SIGHTING_TERMS = (
    "spotted",
    "sighted",
    "reported seeing",
    "possibly spotted",
    "may have been seen",
    "believed to be seen",
    "unconfirmed sighting",
    "was seen at",
    "was seen in",
)
_PRECISE_LOCATION_TERMS = (
    "intersection",
    "street",
    "avenue",
    "boulevard",
    "highway",
    "station",
    "terminal",
    "mall",
    "park",
    "hospital",
    "school",
    "rue",
    "autoroute",
)
_MOVEMENT_TERMS = (
    "heading",
    "toward",
    "travelling",
    "traveling",
    "northbound",
    "southbound",
    "eastbound",
    "westbound",
    "direction:",
)
_VEHICLE_TERMS = ("vehicle:", "licence plate:", "license plate:", "plate:")
_AMPLIFICATION_TERMS = ("please share", "please rt", "retweet", "share this alert")


def _get(lead: Any, key: str, default: Any = None) -> Any:
    if isinstance(lead, Mapping):
        return lead.get(key, default)
    return getattr(lead, key, default)


def _rationale(lead: Any) -> list[str]:
    return [str(item) for item in (_get(lead, "rationale", []) or [])]


def _rationale_values(items: list[str], prefix: str) -> list[str]:
    prefix_lower = prefix.lower()
    return [
        item.split(":", 1)[1].strip()
        for item in items
        if item.lower().startswith(prefix_lower) and ":" in item
    ]


def assess_lead(lead: Any) -> dict[str, Any]:
    """Return analyst-facing evidence semantics for a lead."""

    rationale = _rationale(lead)
    rationale_blob = " ".join(rationale).lower()
    text_blob = " ".join(
        str(_get(lead, key, "") or "")
        for key in ("title", "summary", "content_excerpt", "location_text")
    ).lower()
    lead_type = str(_get(lead, "lead_type", "") or "").lower()
    source_kind = str(_get(lead, "source_kind", "") or "").lower()
    review_status = str(_get(lead, "review_status", "unreviewed") or "unreviewed").lower()
    location_text = _get(lead, "location_text")
    has_coordinates = _get(lead, "latitude") is not None and _get(lead, "longitude") is not None
    source_url = str(_get(lead, "source_url", "") or "").strip()
    parsed_source = urlparse(source_url)
    is_source_backed = parsed_source.scheme in {"http", "https"} and bool(parsed_source.netloc)

    machine_sighting = "machine-extracted sighting location (unverified):" in rationale_blob
    explicit_sighting = machine_sighting or any(term in text_blob for term in _SIGHTING_TERMS)
    identity_ambiguous = any(
        marker in rationale_blob
        for marker in (
            "possible namesake",
            "ambiguous single-name result",
            "likely about a different person",
        )
    )
    official_last_known = lead_type == "official-last-seen"
    tool_only = lead_type in {"analyst-action", "tip-line", "portal-link", "reverse-image-link"}

    if official_last_known:
        evidence_type = "official_last_known"
        evidence_label = "Official last-known location"
    elif tool_only:
        evidence_type = "research_tool"
        evidence_label = "Research or reporting tool"
    elif explicit_sighting and location_text and not identity_ambiguous and is_source_backed:
        evidence_type = "reported_sighting"
        evidence_label = "Reported sighting location"
    elif explicit_sighting and location_text:
        evidence_type = "location_mention"
        evidence_label = (
            "Possible namesake location mention"
            if identity_ambiguous
            else "Unattributed sighting mention"
        )
    elif location_text:
        evidence_type = "location_mention"
        evidence_label = "Location mention"
    else:
        evidence_type = "context_only"
        evidence_label = "Context only"

    if review_status == "credible":
        verification_state = "analyst_reviewed"
        verification_label = "Analyst reviewed"
    elif official_last_known or source_kind == "official":
        verification_state = "official_source"
        verification_label = "Official source"
    else:
        verification_state = "unverified"
        verification_label = "Unverified"

    if evidence_type == "reported_sighting":
        actionability = 55
    elif evidence_type == "official_last_known":
        actionability = 50
    elif evidence_type == "location_mention":
        actionability = 30
    elif evidence_type == "research_tool":
        actionability = 15
    else:
        actionability = 8

    if has_coordinates:
        actionability += 10
    if any(term in text_blob or term in rationale_blob for term in _PRECISE_LOCATION_TERMS):
        actionability += 10
    if any(term in text_blob or term in rationale_blob for term in _MOVEMENT_TERMS):
        actionability += 8
    if any(term in text_blob or term in rationale_blob for term in _VEHICLE_TERMS):
        actionability += 10
    if _get(lead, "published_at"):
        actionability += 4
    if review_status == "credible":
        actionability += 8
    if any(term in text_blob for term in _AMPLIFICATION_TERMS) and evidence_type == "context_only":
        actionability = min(actionability, 10)
    if identity_ambiguous:
        actionability = min(actionability, 20)
    if explicit_sighting and not is_source_backed:
        actionability = min(actionability, 30)

    relevance = _get(lead, "confidence")
    if relevance is not None and source_kind != "official":
        try:
            relevance_score = float(relevance)
        except (TypeError, ValueError):
            relevance_score = 0.0
        if relevance_score < 0.25:
            actionability = min(actionability, 15)
        elif relevance_score < 0.4 and evidence_type == "location_mention":
            actionability = min(actionability, 25)

    actionability = max(0, min(100, actionability))
    actionability_label = "high" if actionability >= 65 else "medium" if actionability >= 40 else "low"

    geocode_precision = _rationale_values(rationale, "Geocode precision:")
    if geocode_precision:
        location_precision = geocode_precision[0].lower()
    elif location_text and any(term in text_blob for term in _PRECISE_LOCATION_TERMS):
        location_precision = "street_or_landmark"
    elif location_text:
        location_precision = "city_or_area"
    else:
        location_precision = "none"

    if evidence_type == "official_last_known":
        location_confidence = 0.95
    elif evidence_type == "reported_sighting":
        location_confidence = 0.65 if review_status == "credible" else 0.45
    elif evidence_type == "location_mention":
        location_confidence = 0.25
    else:
        location_confidence = 0.0

    if identity_ambiguous:
        next_step = (
            "Resolve the identity conflict before using this location; the returned text may describe a namesake."
        )
    elif evidence_type == "reported_sighting":
        next_step = (
            "Verify that the report describes the subject, date, and place; compare distinctive details, "
            "then send the source URL and extracted facts to the investigating authority."
        )
    elif evidence_type == "location_mention":
        next_step = (
            "Open the source and determine whether the place is a new sighting, the original case location, "
            "or incidental context."
        )
    elif evidence_type == "official_last_known":
        next_step = "Use this official location as the baseline for timelines and travel-corridor comparisons."
    elif evidence_type == "research_tool":
        next_step = "Use this link as an analyst tool only; it is not evidence or a sighting."
    else:
        next_step = "Retain only if manual review finds a new date, place, witness detail, vehicle, or direction of travel."

    extracted_details = [
        item
        for item in rationale
        if item.lower().startswith(
            (
                "machine-extracted",
                "geocoded public place",
                "physical description:",
                "clothing:",
                "direction:",
                "vehicle:",
                "licence plate:",
            )
        )
    ]

    return {
        "evidence_type": evidence_type,
        "evidence_label": evidence_label,
        "verification_state": verification_state,
        "verification_label": verification_label,
        "actionability_score": actionability,
        "actionability_label": actionability_label,
        "is_location_candidate": evidence_type == "reported_sighting" and bool(location_text),
        "location_confidence": location_confidence,
        "location_precision": location_precision,
        "has_coordinates": has_coordinates,
        "is_source_backed": is_source_backed,
        "next_step": next_step,
        "extracted_details": extracted_details,
    }
