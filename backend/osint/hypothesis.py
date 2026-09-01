"""MAAT Hypothesis Engine — Trace Labs-inspired analytical reasoning.

Takes all gathered intelligence (leads, synthesis clusters, geospatial context,
case demographics) and produces:
  1. Behavioral profile based on age / demographics / location
  2. Scenario analysis with weighted evidence (runaway, abduction, etc.)
  3. Geographic probability (where they most likely are / went)
  4. Conclusive educated guess with confidence level

This is the "thinking" layer that transforms raw OSINT leads into
actionable analytical conclusions — the core of what Trace Labs volunteers
do during CTFs when they reason about accumulated evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from shared.utils.geo import haversine_km


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Scenario:
    """A possible explanation for the disappearance."""
    name: str
    description: str
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    weight: float = 0.0  # 0-1, computed from evidence balance
    confidence: str = ""  # low, medium, high


@dataclass(slots=True)
class GeographicAssessment:
    """Where the person most likely is or traveled to."""
    probable_zone: str
    description: str
    supporting_leads: int = 0
    distance_from_origin_km: float | None = None
    nearby_infrastructure: list[str] = field(default_factory=list)
    confidence: str = "low"


@dataclass(slots=True)
class HypothesisReport:
    """Full analytical conclusion for a missing person case."""
    case_id: int
    case_name: str
    generated_at: str

    # Behavioral profile
    demographic_profile: str
    behavioral_indicators: list[str]

    # Scenarios
    scenarios: list[Scenario]
    primary_scenario: str
    primary_scenario_confidence: str

    # Geographic assessment
    geographic_assessment: GeographicAssessment
    travel_indicators: list[str]

    # Final conclusion
    conclusion: str
    confidence_level: str  # low, medium, high
    key_evidence_summary: list[str]
    recommended_search_areas: list[str]
    critical_actions: list[str]

    # Metadata
    evidence_count: int = 0
    data_quality_note: str = ""


# ---------------------------------------------------------------------------
# Age and demographic profiling
# ---------------------------------------------------------------------------

_AGE_PROFILES = {
    "young_child": {
        "range": (0, 11),
        "profile": "Young child — unlikely to travel independently. Disappearance almost certainly involves a third party (custodial dispute, abduction, or accident).",
        "behavioral": [
            "Cannot travel independently — requires adult assistance or transportation.",
            "Limited to walking distance from last known location if alone.",
            "High likelihood of custodial/family involvement.",
            "May be with a non-custodial parent or family member.",
        ],
        "scenarios_weight": {"custodial_dispute": 0.40, "abduction": 0.30, "accident": 0.20, "runaway": 0.0, "voluntary_departure": 0.0, "exploitation": 0.10},
    },
    "young_teen": {
        "range": (12, 14),
        "profile": "Young teenager — capable of limited independent travel but typically stays within familiar social circles. May have access to transit or rides from older peers.",
        "behavioral": [
            "Can use public transit but typically stays within known areas.",
            "Strong peer influence — likely to seek friends or romantic interests.",
            "Social media is primary communication channel.",
            "May frequent malls, parks, bus stations, or friends' houses.",
            "School connections are the primary social network.",
        ],
        "scenarios_weight": {"runaway": 0.40, "exploitation": 0.20, "custodial_dispute": 0.15, "voluntary_departure": 0.15, "abduction": 0.05, "accident": 0.05},
    },
    "older_teen": {
        "range": (15, 17),
        "profile": "Older teenager — significant independent mobility. May have access to vehicles, transit passes, or inter-city travel. Stronger online presence and peer network.",
        "behavioral": [
            "Can travel inter-city via bus, train, or rides from peers.",
            "Active social media presence — may leave digital footprints.",
            "Romantic relationships may be a factor.",
            "Employment or part-time work connections exist.",
            "May seek independence or flee difficult home situations.",
            "Risk of exploitation or trafficking, especially if vulnerable.",
        ],
        "scenarios_weight": {"runaway": 0.45, "exploitation": 0.20, "voluntary_departure": 0.15, "custodial_dispute": 0.05, "abduction": 0.05, "accident": 0.10},
    },
    "young_adult": {
        "range": (18, 25),
        "profile": "Young adult — full independent mobility. May have employment, vehicle, and established social networks. Disappearance may be voluntary or involve complex circumstances.",
        "behavioral": [
            "Full access to transportation including personal vehicles.",
            "Employment and financial records may exist.",
            "May have left voluntarily due to personal circumstances.",
            "Substance issues or mental health factors may be involved.",
            "May have traveled significant distances.",
        ],
        "scenarios_weight": {"voluntary_departure": 0.35, "runaway": 0.20, "accident": 0.15, "exploitation": 0.10, "abduction": 0.10, "custodial_dispute": 0.10},
    },
    "adult": {
        "range": (26, 64),
        "profile": "Adult — full autonomy. Disappearance may be voluntary, related to personal crisis, or involve foul play.",
        "behavioral": [
            "Full independence and resources for travel.",
            "Employment disruption or financial stress may be factors.",
            "Personal relationships and domestic situations are key context.",
            "May have chosen to disappear voluntarily.",
        ],
        "scenarios_weight": {"voluntary_departure": 0.40, "accident": 0.20, "foul_play": 0.20, "exploitation": 0.10, "abduction": 0.10},
    },
    "senior": {
        "range": (65, 200),
        "profile": "Senior — may have cognitive or medical vulnerabilities. Disappearance may involve disorientation, medical emergency, or voluntary travel with diminished situational awareness.",
        "behavioral": [
            "May have dementia, Alzheimer's, or other cognitive conditions.",
            "Driving ability may be impaired — higher accident risk.",
            "Likely has established routines — deviation is significant.",
            "Medical needs may become urgent if separated from medication.",
            "May be confused about location or unable to seek help.",
        ],
        "scenarios_weight": {"accident": 0.35, "voluntary_departure": 0.25, "medical_emergency": 0.20, "foul_play": 0.10, "exploitation": 0.10},
    },
}

_SCENARIO_DESCRIPTIONS = {
    "runaway": "Subject left voluntarily, likely due to home situation, peer influence, or desire for independence. May be staying with friends, partners, or at shelters.",
    "exploitation": "Subject may be a victim of exploitation or trafficking. Vulnerabilities include age, home instability, online contacts, and previous incidents.",
    "custodial_dispute": "Subject may be with a non-custodial parent or family member who has taken them without authorization.",
    "abduction": "Subject was taken against their will by a known or unknown person. This is a high-urgency scenario.",
    "voluntary_departure": "Subject chose to leave and is maintaining distance deliberately. May be in another city or staying off-grid.",
    "accident": "Subject may have been involved in an accident (wilderness, water, vehicle) and is unable to communicate.",
    "foul_play": "Evidence suggests possible criminal involvement in the disappearance.",
    "medical_emergency": "Subject may be experiencing a medical or cognitive emergency and is unable to communicate or navigate home.",
}


def _get_age_profile(age: int | None) -> dict:
    """Return the demographic profile for a given age."""
    if age is None:
        return _AGE_PROFILES["older_teen"]  # Default assumption for missing children in Canada
    for key, profile in _AGE_PROFILES.items():
        low, high = profile["range"]
        if low <= age <= high:
            return profile
    return _AGE_PROFILES["adult"]


# ---------------------------------------------------------------------------
# Evidence analysis
# ---------------------------------------------------------------------------

def _analyze_lead_evidence(
    leads: list[dict],
    case_name: str,
    case_city: str | None,
    case_province: str | None,
) -> dict[str, Any]:
    """Extract investigative signals from leads."""
    signals: dict[str, Any] = {
        "total_leads": len(leads),
        "high_confidence": 0,
        "social_media_leads": [],
        "news_leads": [],
        "sighting_leads": [],
        "family_leads": [],
        "community_leads": [],
        "network_leads": [],
        "locations_mentioned": [],
        "has_gofundme": False,
        "has_social_profiles": False,
        "has_school_connection": False,
        "has_employment": False,
        "has_sightings": False,
        "has_family_appeal": False,
        "has_community_search": False,
        "has_travel_indicators": False,
        "lead_categories": defaultdict(int),
        "lead_types": defaultdict(int),
        "source_diversity": set(),
        "date_range": None,
    }

    for lead in leads:
        conf = lead.get("confidence", 0)
        if conf >= 0.5:
            signals["high_confidence"] += 1

        category = lead.get("category", "")
        lead_type = lead.get("lead_type", "")
        source = lead.get("source_name", "")
        title = (lead.get("title") or "").lower()
        excerpt = (lead.get("content_excerpt") or "").lower()
        text = f"{title} {excerpt}"

        signals["lead_categories"][category] += 1
        signals["lead_types"][lead_type] += 1
        signals["source_diversity"].add(source)

        # Classify by investigative value
        if lead_type in ("social-profile", "username-match", "cross-platform-pivot"):
            signals["social_media_leads"].append(lead)
            signals["has_social_profiles"] = True
        elif lead_type in ("family-network", "network-connection"):
            signals["family_leads"].append(lead)
        elif lead_type in ("community-appeal", "community-context"):
            signals["community_leads"].append(lead)
        elif category == "network-behavioral":
            signals["network_leads"].append(lead)
        elif lead_type == "news-article":
            signals["news_leads"].append(lead)

        # Signal extraction from content
        if "gofundme" in text or "fundraiser" in text:
            signals["has_gofundme"] = True
            signals["has_family_appeal"] = True
        if "school" in text or "class of" in text or "graduation" in text:
            signals["has_school_connection"] = True
        if "work" in text or "employed" in text or "linkedin" in text:
            signals["has_employment"] = True
        # "Last seen" is part of the official baseline and must not be
        # counted as a new public sighting. Only explicit current-sighting
        # language should influence scenario analysis and search areas.
        current_sighting_terms = (
            "spotted", "sighted", "reported seeing", "possibly seen",
            "may have been seen", "believed to be seen", "unconfirmed sighting",
        )
        # A headline containing "spotted" is not enough on its own: broad
        # first-name searches routinely return unrelated missing-person
        # stories. Keep only reasonably case-linked candidates in scenario
        # analysis; lower-confidence mentions remain available for review.
        case_linked = (
            lead.get("lead_type") == "sighting-trace"
            or (
                conf >= 0.6
                and case_name.lower() in text
                and (case_city or "").lower() in text
            )
        )
        if case_linked and any(term in text for term in current_sighting_terms):
            signals["has_sightings"] = True
            signals["sighting_leads"].append(lead)
        if "search party" in text or "volunteer" in text:
            signals["has_community_search"] = True
        if any(kw in text for kw in ("bus", "greyhound", "train", "travel", "ride", "highway")):
            signals["has_travel_indicators"] = True
        if any(kw in text for kw in ("family", "mother", "father", "mom", "dad", "parent")):
            signals["has_family_appeal"] = True

        # Location extraction
        loc = lead.get("location_text")
        if loc and loc.strip():
            signals["locations_mentioned"].append(loc.strip())

    signals["source_diversity"] = list(signals["source_diversity"])
    return signals


def _extract_sighting_locations(signals: dict[str, Any]) -> list[str]:
    """Extract unique sighting-related location names from leads."""
    locations: list[str] = []
    seen = set()
    for lead in signals.get("sighting_leads", []):
        for r in lead.get("rationale", []):
            r_str = str(r)
            if (
                "Machine-extracted sighting location" in r_str
                or "Sighting location" in r_str
                or "Title sighting location" in r_str
            ):
                # Extract city name after the colon
                parts = r_str.split(":", 1)
                if len(parts) == 2:
                    city = parts[1].strip()
                    if city.lower() not in seen:
                        seen.add(city.lower())
                        locations.append(city)
    return locations


def _build_scenarios(
    age_profile: dict,
    signals: dict[str, Any],
    case_age: int | None,
    missing_since: datetime | None,
    geo_context: list[dict],
) -> list[Scenario]:
    """Build and weight scenarios based on evidence."""
    base_weights = dict(age_profile.get("scenarios_weight", {}))
    scenarios: list[Scenario] = []

    # Calculate days missing
    days_missing = None
    if missing_since:
        now = datetime.now(timezone.utc)
        ms = missing_since if missing_since.tzinfo else missing_since.replace(tzinfo=timezone.utc)
        days_missing = (now - ms).days

    for scenario_name, base_weight in base_weights.items():
        desc = _SCENARIO_DESCRIPTIONS.get(scenario_name, "")
        evidence_for: list[str] = []
        evidence_against: list[str] = []
        weight_adjustment = 0.0

        # ── Runaway scenario ──
        if scenario_name == "runaway":
            if signals["has_social_profiles"]:
                evidence_for.append("Social media profiles found — may indicate active digital life and peer connections.")
                weight_adjustment += 0.05
            if signals["has_school_connection"]:
                evidence_for.append("School connections identified — peer network exists for potential shelter.")
                weight_adjustment += 0.03
            if signals["has_travel_indicators"]:
                evidence_for.append("Travel-related content found — subject may have left the area.")
                weight_adjustment += 0.05
            if case_age is not None and 14 <= case_age <= 17:
                evidence_for.append(f"Age {case_age} is in the highest-risk bracket for voluntary departure.")
                weight_adjustment += 0.05
            if days_missing and days_missing > 30:
                evidence_for.append(f"Missing for {days_missing} days — sustained absence suggests deliberate departure.")
                weight_adjustment += 0.03

            # Evidence against
            if case_age is not None and case_age < 12:
                evidence_against.append("Subject is too young for independent runaway behavior.")
                weight_adjustment -= 0.20
            if not signals["has_social_profiles"] and not signals["has_school_connection"]:
                evidence_against.append("No social media or school connections found — limited evidence of independent social life.")
                weight_adjustment -= 0.05

        # ── Exploitation scenario ──
        elif scenario_name == "exploitation":
            # Exploitation/grooming is primarily a concern for youth; suppress for seniors
            if case_age is not None and case_age >= 65:
                evidence_against.append(f"Age {case_age} — exploitation/grooming is not a primary concern for seniors.")
                weight_adjustment -= 0.15
            elif case_age is not None and 13 <= case_age <= 17:
                evidence_for.append(f"Age {case_age} is in a vulnerable demographic for exploitation.")
                weight_adjustment += 0.05
            if signals["has_social_profiles"] and (case_age is None or case_age < 65):
                evidence_for.append("Active social media presence — potential grooming vector.")
                weight_adjustment += 0.03
            if signals["has_travel_indicators"]:
                evidence_for.append("Travel indicators present — may have been moved by a third party.")
                weight_adjustment += 0.05
            if days_missing and days_missing > 14:
                evidence_for.append(f"Extended absence ({days_missing} days) increases exploitation concern.")
                weight_adjustment += 0.03

            # Check for nearby transit / border
            for geo in geo_context:
                ct = geo.get("context_type", "")
                dist = geo.get("distance_km", 9999)
                if ct == "border-crossing" and dist < 100:
                    evidence_for.append(f"Border crossing within {dist:.0f} km — cross-border trafficking risk.")
                    weight_adjustment += 0.05
                if ct == "highway" and dist < 20:
                    evidence_for.append(f"Major highway within {dist:.0f} km — facilitates rapid movement.")
                    weight_adjustment += 0.02

        # ── Custodial dispute ──
        elif scenario_name == "custodial_dispute":
            if signals["has_family_appeal"]:
                evidence_for.append("Family appeals found — indicates family awareness and concern.")
            if case_age is not None and case_age < 12:
                evidence_for.append(f"Age {case_age} — young children are more commonly involved in custodial disputes.")
                weight_adjustment += 0.05

        # ── Abduction ──
        elif scenario_name == "abduction":
            if signals["has_community_search"]:
                evidence_for.append("Organized community search — suggests urgency consistent with abduction concern.")
                weight_adjustment += 0.05
            if case_age is not None and case_age < 10:
                evidence_for.append(f"Age {case_age} — young children have higher abduction risk.")
                weight_adjustment += 0.05
            # Most abductions are resolved quickly
            if days_missing and days_missing > 60:
                evidence_against.append(f"Extended timeline ({days_missing} days) — most stranger abductions are resolved or escalated rapidly.")
                weight_adjustment -= 0.05

        # ── Voluntary departure ──
        elif scenario_name == "voluntary_departure":
            if case_age is not None and case_age >= 16:
                evidence_for.append(f"Age {case_age} — capable of deliberate independent departure.")
                weight_adjustment += 0.03
            if signals["has_employment"] and (case_age is None or case_age < 65):
                evidence_for.append("Employment indicators found — may have financial means to sustain departure.")
                weight_adjustment += 0.05
            if not signals["has_sightings"]:
                evidence_for.append("No public sightings reported — consistent with deliberate avoidance.")
                weight_adjustment += 0.03
            if signals["has_sightings"]:
                evidence_against.append("Public sightings reported — not typical of someone deliberately hiding.")
                weight_adjustment -= 0.05
            # Seniors are less likely to voluntarily disappear
            if case_age is not None and case_age >= 65:
                evidence_against.append(f"Age {case_age} — seniors rarely depart voluntarily without informing family.")
                weight_adjustment -= 0.10

        # ── Accident ──
        elif scenario_name == "accident":
            for geo in geo_context:
                ct = geo.get("context_type", "")
                if ct == "highway":
                    evidence_for.append(f"Proximity to highway infrastructure — vehicle accident risk.")
                    weight_adjustment += 0.02
            if case_age is not None and case_age >= 70:
                evidence_for.append(f"Age {case_age} — elevated accident risk for elderly drivers.")
                weight_adjustment += 0.05
            if signals["has_sightings"] and signals["has_travel_indicators"]:
                evidence_for.append("Active travel with sightings — subject is mobile but may become involved in a road incident.")
                weight_adjustment += 0.03

        # ── Medical emergency ──
        elif scenario_name == "medical_emergency":
            if case_age is not None and case_age >= 70:
                evidence_for.append(f"Age {case_age} — elevated risk of cognitive or medical emergency.")
                weight_adjustment += 0.05
            if signals["has_sightings"]:
                evidence_for.append("Sightings reported — subject is mobile but may be disoriented.")
                weight_adjustment += 0.05
            if signals["has_travel_indicators"]:
                evidence_for.append("Travel indicators suggest subject is moving — may be driving while confused.")
                weight_adjustment += 0.05

        # ── Foul play ──
        elif scenario_name == "foul_play":
            if signals["has_community_search"]:
                evidence_for.append("Organized search suggests authorities take this seriously.")
                weight_adjustment += 0.03

        # Compute final weight
        final_weight = max(0.0, min(1.0, base_weight + weight_adjustment))

        # Confidence mapping
        if len(evidence_for) >= 3 and final_weight >= 0.3:
            confidence = "high"
        elif len(evidence_for) >= 1 and final_weight >= 0.15:
            confidence = "medium"
        else:
            confidence = "low"

        scenarios.append(Scenario(
            name=scenario_name.replace("_", " ").title(),
            description=desc,
            evidence_for=evidence_for,
            evidence_against=evidence_against,
            weight=round(final_weight, 3),
            confidence=confidence,
        ))

    # Sort by weight descending
    scenarios.sort(key=lambda s: s.weight, reverse=True)
    return scenarios


def _build_geographic_assessment(
    signals: dict[str, Any],
    case_city: str | None,
    case_province: str | None,
    case_lat: float | None,
    case_lon: float | None,
    geo_context: list[dict],
    scenarios: list[Scenario],
) -> GeographicAssessment:
    """Assess where the person most likely is."""

    primary = scenarios[0].name if scenarios else "Unknown"
    locations = signals.get("locations_mentioned", [])
    unique_locations = list(set(locations))

    infrastructure: list[str] = []
    for geo in geo_context:
        label = geo.get("label", "")
        ct = geo.get("context_type", "")
        dist = geo.get("distance_km")
        if dist is not None and dist < 150:
            infrastructure.append(f"{ct}: {label} ({dist:.0f} km)")

    # Determine probable zone based on sighting data and primary scenario
    sighting_locs = _extract_sighting_locations(signals)
    if sighting_locs:
        # Sighting data overrides scenario-based guessing
        loc_str = ", ".join(sighting_locs[:5])
        zone = f"Unverified sighting report(s) mention: {loc_str}. Verify each source before comparing corridors with {case_city or 'the last known location'}."
        confidence = "high" if len(sighting_locs) >= 2 else "medium"
    elif "Runaway" in primary:
        if signals["has_travel_indicators"]:
            zone = f"Likely beyond {case_city or 'origin'} — travel indicators suggest inter-city movement. Check bus stations, shelters, and friends' addresses in nearby cities."
            confidence = "medium"
        else:
            zone = f"Likely still within {case_city or case_province or 'the region'} — staying with friends, at shelters, or in familiar locations (malls, parks, community centers)."
            confidence = "medium"
    elif "Exploitation" in primary:
        zone = f"May have been moved from {case_city or 'origin'}. Check nearby urban centers, transit corridors, and border crossings. Shelters and outreach services should be alerted."
        confidence = "low"
    elif "Custodial" in primary:
        zone = f"Likely with a family member — check addresses of non-custodial parents and extended family in {case_province or 'the province'} and adjacent provinces."
        confidence = "medium"
    elif "Voluntary" in primary:
        zone = f"Likely in another city — the subject has the means to sustain a departure. Check employment records, social media activity, and financial traces."
        confidence = "low"
    elif "Abduction" in primary:
        zone = "Immediate area and transit corridors should be priority. Time-sensitive — widening search radius is critical."
        confidence = "low"
    elif "Medical Emergency" in primary:
        zone = f"Subject may be stranded or disoriented along travel route from {case_city or 'origin'}. Check hospitals, care facilities, and roadside stops. Alert local pharmacies if medication needs are known."
        confidence = "medium"
    else:
        zone = f"Insufficient evidence to narrow geographic probability. Focus on {case_city or case_province or 'the reported area'} and expand based on new leads."
        confidence = "low"

    return GeographicAssessment(
        probable_zone=zone,
        description=f"Based on {len(unique_locations)} unique location mentions and {len(infrastructure)} nearby infrastructure points.",
        supporting_leads=len(signals.get("sighting_leads", [])),
        nearby_infrastructure=infrastructure,
        confidence=confidence,
    )


def _build_conclusion(
    case_name: str,
    case_age: int | None,
    case_city: str | None,
    case_province: str | None,
    missing_since: datetime | None,
    scenarios: list[Scenario],
    geo_assessment: GeographicAssessment,
    signals: dict[str, Any],
) -> tuple[str, str, list[str], list[str], list[str]]:
    """Build the final educated conclusion."""

    primary = scenarios[0] if scenarios else None
    days_missing = None
    if missing_since:
        now = datetime.now(timezone.utc)
        ms = missing_since if missing_since.tzinfo else missing_since.replace(tzinfo=timezone.utc)
        days_missing = (now - ms).days

    # Key evidence summary
    key_evidence: list[str] = []
    if signals["total_leads"] > 0:
        key_evidence.append(f"Analysis based on {signals['total_leads']} OSINT leads from {len(signals['source_diversity'])} distinct sources.")
    if signals["high_confidence"] > 0:
        key_evidence.append(f"{signals['high_confidence']} high-confidence leads identified.")
    if signals["has_social_profiles"]:
        key_evidence.append(f"Social media profiles discovered ({len(signals['social_media_leads'])} leads) — digital footprint exists.")
    if signals["has_family_appeal"]:
        key_evidence.append("Family/community appeals detected — active awareness campaign exists.")
    if signals["has_sightings"]:
        key_evidence.append(f"Potential sighting reports found ({len(signals['sighting_leads'])} leads).")
    if signals["has_travel_indicators"]:
        key_evidence.append("Travel/transportation indicators detected in lead content.")
    if signals["has_school_connection"]:
        key_evidence.append("School/educational connections identified.")

    # Recommended search areas
    search_areas: list[str] = []
    if case_city:
        search_areas.append(f"{case_city} and immediate surroundings (last known location)")
    # Add sighting-derived locations to search areas
    sighting_locations = _extract_sighting_locations(signals)
    if sighting_locations:
        search_areas.append(f"Unverified reported sighting area(s): {', '.join(sighting_locations[:5])}")
    if signals["has_travel_indicators"]:
        search_areas.append("Highway rest stops, gas stations, and transit hubs along the travel route")
    # Age-appropriate shelter recommendations
    if case_age is not None and case_age >= 65:
        search_areas.append("Hospitals, care facilities, and senior services in the region")
    elif case_age is not None and case_age < 18:
        search_areas.append("Youth shelters and outreach services in the province")
    else:
        search_areas.append("Shelters, hospitals, and outreach services in the province")
    for geo in (geo_assessment.nearby_infrastructure or [])[:3]:
        search_areas.append(f"Near {geo}")

    # Critical actions
    critical_actions: list[str] = []
    critical_actions.append("Forward all leads to the investigating authority for verification.")
    if signals["has_social_profiles"]:
        critical_actions.append("Monitor identified social media profiles for new activity (requires authority authorization).")
    if signals["has_sightings"]:
        critical_actions.append("Verify sighting reports by cross-referencing dates, locations, and descriptions.")
    if primary and "Exploitation" in primary.name:
        critical_actions.append("Alert human trafficking units and border services.")
    critical_actions.append("Check local shelters, hospitals, and outreach programs.")

    # Build conclusion text
    if not primary:
        conclusion = f"Insufficient data to form a hypothesis about {case_name}'s disappearance."
        confidence = "low"
        return conclusion, confidence, key_evidence, search_areas, critical_actions

    lines: list[str] = []
    lines.append(f"MAAT ANALYTICAL ASSESSMENT — {case_name}")
    lines.append("")

    if days_missing:
        lines.append(f"Subject has been missing for {days_missing} days from {case_city or 'an unspecified location'}, {case_province or 'Canada'}.")
    if case_age:
        lines.append(f"Subject age: {case_age}.")
    lines.append("")

    lines.append(f"PRIMARY HYPOTHESIS: {primary.name} (confidence: {primary.confidence})")
    lines.append(f"  {primary.description}")
    lines.append("")

    if primary.evidence_for:
        lines.append("SUPPORTING EVIDENCE:")
        for ev in primary.evidence_for:
            lines.append(f"  + {ev}")
    if primary.evidence_against:
        lines.append("CONTRARY INDICATORS:")
        for ev in primary.evidence_against:
            lines.append(f"  - {ev}")

    lines.append("")
    lines.append(f"GEOGRAPHIC ASSESSMENT: {geo_assessment.probable_zone}")
    lines.append("")

    # Secondary scenario if close to primary
    if len(scenarios) >= 2 and scenarios[1].weight >= scenarios[0].weight * 0.5:
        secondary = scenarios[1]
        lines.append(f"ALTERNATIVE HYPOTHESIS: {secondary.name} (weight: {secondary.weight:.2f})")
        lines.append(f"  {secondary.description}")
        if secondary.evidence_for:
            for ev in secondary.evidence_for[:2]:
                lines.append(f"  + {ev}")
        lines.append("")

    lines.append("─" * 60)
    lines.append("NOTE: This assessment is generated from publicly available")
    lines.append("information only. All hypotheses require verification by")
    lines.append("law enforcement. No contact has been made with subjects,")
    lines.append("relatives, or witnesses.")

    conclusion = "\n".join(lines)

    # Overall confidence
    if signals["total_leads"] >= 15 and signals["high_confidence"] >= 3:
        confidence = "medium-high"
    elif signals["total_leads"] >= 8 and signals["high_confidence"] >= 1:
        confidence = "medium"
    elif signals["total_leads"] >= 3:
        confidence = "low-medium"
    else:
        confidence = "low"

    return conclusion, confidence, key_evidence, search_areas, critical_actions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_hypothesis(
    case_id: int,
    case_name: str,
    case_age: int | None,
    case_city: str | None,
    case_province: str | None,
    case_lat: float | None,
    case_lon: float | None,
    missing_since: datetime | None,
    leads: list[dict],
    geo_context: list[dict] | None = None,
) -> HypothesisReport:
    """Generate an analytical hypothesis about a missing person case.

    This is the "educated guess" — the Trace Labs-inspired reasoning
    that transforms raw OSINT into an actionable conclusion.
    """
    geo_context = geo_context or []

    # Step 1: Demographic profiling
    age_profile = _get_age_profile(case_age)

    # Step 2: Extract investigative signals from leads
    signals = _analyze_lead_evidence(leads, case_name or "", case_city, case_province)

    # Step 3: Build weighted scenarios
    scenarios = _build_scenarios(
        age_profile, signals, case_age, missing_since, geo_context,
    )

    # Step 4: Geographic probability
    geo_assessment = _build_geographic_assessment(
        signals, case_city, case_province, case_lat, case_lon, geo_context, scenarios,
    )

    # Step 5: Build final conclusion
    conclusion, confidence, key_evidence, search_areas, critical_actions = _build_conclusion(
        case_name or "Unknown",
        case_age,
        case_city,
        case_province,
        missing_since,
        scenarios,
        geo_assessment,
        signals,
    )

    # Data quality note
    quality_notes: list[str] = []
    if signals["total_leads"] < 5:
        quality_notes.append("Limited lead volume — conclusions are tentative.")
    if len(signals["source_diversity"]) < 3:
        quality_notes.append("Low source diversity — corroboration is limited.")
    if not signals["has_social_profiles"]:
        quality_notes.append("No social media profiles found — digital footprint analysis incomplete.")
    data_quality = " ".join(quality_notes) if quality_notes else "Adequate data volume for preliminary assessment."

    return HypothesisReport(
        case_id=case_id,
        case_name=case_name or "Unknown",
        generated_at=datetime.now(timezone.utc).isoformat(),
        demographic_profile=age_profile["profile"],
        behavioral_indicators=list(age_profile["behavioral"]),
        scenarios=scenarios,
        primary_scenario=scenarios[0].name if scenarios else "Unknown",
        primary_scenario_confidence=scenarios[0].confidence if scenarios else "low",
        geographic_assessment=geo_assessment,
        travel_indicators=[
            ind for ind in [
                "Travel content detected in leads" if signals["has_travel_indicators"] else None,
                f"Sighting reports: {len(signals['sighting_leads'])}" if signals["has_sightings"] else None,
            ] if ind
        ],
        conclusion=conclusion,
        confidence_level=confidence,
        key_evidence_summary=key_evidence,
        recommended_search_areas=search_areas,
        critical_actions=critical_actions,
        evidence_count=signals["total_leads"],
        data_quality_note=data_quality,
    )
