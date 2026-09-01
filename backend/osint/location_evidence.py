"""Evaluate whether public sighting leads converge on a usable location.

This gate is deliberately stricter than lead relevance. A location conclusion
needs attributable URLs, independent source domains, geographic agreement, and
at least one analyst-reviewed or official source.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from backend.osint.lead_analysis import assess_lead
from shared.utils.geo import haversine_km


def _source_identity(lead: dict[str, Any]) -> str:
    host = urlparse(str(lead.get("source_url") or "")).hostname
    return (host or "").lower().removeprefix("www.")


def _location_key(lead: dict[str, Any]) -> str:
    value = str(lead.get("location_text") or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _reviewed(lead: dict[str, Any], analysis: dict[str, Any]) -> bool:
    return (
        str(lead.get("review_status") or "").lower() == "credible"
        or analysis.get("verification_state") in {"analyst_reviewed", "official_source"}
    )


def _same_location(left: dict[str, Any], right: dict[str, Any], radius_km: float) -> bool:
    coords = (
        left.get("latitude"), left.get("longitude"),
        right.get("latitude"), right.get("longitude"),
    )
    if all(value is not None for value in coords):
        return haversine_km(coords[0], coords[1], coords[2], coords[3]) <= radius_km
    left_key = _location_key(left)
    return bool(left_key and left_key == _location_key(right))


def evaluate_location_evidence(
    leads: list[dict[str, Any]],
    *,
    radius_km: float = 25.0,
    minimum_independent_sources: int = 2,
) -> dict[str, Any]:
    """Return a serializable convergence report for source-backed sightings."""

    candidates: list[dict[str, Any]] = []
    rejected = 0
    for index, lead in enumerate(leads):
        analysis = lead.get("analysis") or assess_lead(lead)
        if not analysis.get("is_location_candidate"):
            rejected += 1
            continue
        source = _source_identity(lead)
        if not source:
            rejected += 1
            continue
        candidates.append({"index": index, "lead": lead, "analysis": analysis, "source": source})

    clusters: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        match = next(
            (cluster for cluster in clusters if _same_location(candidate["lead"], cluster[0]["lead"], radius_km)),
            None,
        )
        if match is None:
            clusters.append([candidate])
        else:
            match.append(candidate)

    summaries: list[dict[str, Any]] = []
    for cluster in clusters:
        sources = sorted({item["source"] for item in cluster})
        reviewed_count = sum(_reviewed(item["lead"], item["analysis"]) for item in cluster)
        with_coordinates = [
            item for item in cluster
            if item["lead"].get("latitude") is not None and item["lead"].get("longitude") is not None
        ]
        anchor = max(
            cluster,
            key=lambda item: (
                item["analysis"].get("location_precision") in {"street_or_landmark", "neighbourhood"},
                item["lead"].get("confidence", 0),
            ),
        )
        sufficient = len(sources) >= minimum_independent_sources and reviewed_count >= 1
        summaries.append({
            "location": anchor["lead"].get("location_text"),
            "latitude": (
                round(sum(item["lead"]["latitude"] for item in with_coordinates) / len(with_coordinates), 6)
                if with_coordinates else None
            ),
            "longitude": (
                round(sum(item["lead"]["longitude"] for item in with_coordinates) / len(with_coordinates), 6)
                if with_coordinates else None
            ),
            "lead_count": len(cluster),
            "lead_indices": [item["index"] for item in cluster],
            "independent_source_count": len(sources),
            "independent_sources": sources,
            "reviewed_source_count": reviewed_count,
            "sufficient": sufficient,
        })

    summaries.sort(
        key=lambda item: (
            item["sufficient"],
            item["independent_source_count"],
            item["reviewed_source_count"],
            item["lead_count"],
        ),
        reverse=True,
    )
    sufficient_clusters = [item for item in summaries if item["sufficient"]]
    conflicting = len(sufficient_clusters) > 1
    sufficient = len(sufficient_clusters) == 1
    best = sufficient_clusters[0] if sufficient else (summaries[0] if summaries else None)

    if conflicting:
        reason = "Multiple independently corroborated locations conflict; resolve dates and identity before locating."
    elif sufficient:
        reason = "Independent source domains converge and at least one report has been reviewed."
    elif not candidates:
        reason = "No attributable post-disappearance sighting candidates were found."
    elif best and best["independent_source_count"] < minimum_independent_sources:
        reason = "The best location lacks independent source-domain corroboration."
    else:
        reason = "The best location has not been reviewed by an analyst or official source."

    confidence = "high" if sufficient and best["independent_source_count"] >= 3 else "medium" if sufficient else "low"
    return {
        "sufficient": sufficient,
        "confidence": confidence,
        "reason": reason,
        "candidate_count": len(candidates),
        "rejected_count": rejected,
        "conflicting": conflicting,
        "best_candidate": best,
        "clusters": summaries,
    }
