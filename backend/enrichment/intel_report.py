"""Generate structured intelligence reports from investigation results.

Produces a Markdown report that condenses investigation findings into
actionable intelligence: verified sighting locations, physical descriptions,
extracted witness statements, reward info, tip line contacts, and
recommended next actions — organized for quick use by investigators.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _confidence_label(score: float) -> str:
    if score >= 0.6:
        return "HIGH"
    elif score >= 0.35:
        return "MEDIUM"
    return "LOW"


def generate_intel_report(result_path: str | Path, output_path: str | Path | None = None) -> str:
    """Generate an actionable intelligence report from investigation JSON.

    Args:
        result_path: Path to the investigation result JSON file.
        output_path: Where to write the Markdown report. Defaults to same
                     directory as result_path with .md extension.

    Returns:
        The path to the generated report file.
    """
    result_path = Path(result_path)
    with open(result_path) as f:
        data = json.load(f)

    case = data.get("case", {})
    run = data.get("run", {})
    leads = data.get("leads", [])
    synthesis = data.get("synthesis", {})
    hypothesis = data.get("hypothesis", {})

    case_name = case.get("name", "Unknown")
    case_id = case.get("id", "?")
    case_city = case.get("city", "Unknown")
    case_province = case.get("province", "Unknown")
    case_age = case.get("age", "?")
    missing_since = case.get("missing_since", "Unknown")

    lines = []
    lines.append(f"# INTELLIGENCE REPORT: {case_name}")
    lines.append(f"**Case ID:** {case_id}  ")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ")
    lines.append(f"**Classification:** OSINT — Open Source Only  ")
    lines.append(f"**Status:** {run.get('status', 'unknown')}  ")
    lines.append(f"**Total leads analyzed:** {run.get('total_leads', len(leads))}  ")
    lines.append("")

    # ── Subject Profile ──
    lines.append("## 1. SUBJECT PROFILE")
    lines.append(f"- **Name:** {case_name}")
    lines.append(f"- **Age at disappearance:** {case_age}")
    lines.append(f"- **Last seen city:** {case_city}, {case_province}")
    lines.append(f"- **Missing since:** {missing_since}")
    if case.get("authority"):
        lines.append(f"- **Investigating authority:** {case['authority']}")
    if case.get("authority_url"):
        lines.append(f"- **Official URL:** {case['authority_url']}")
    lines.append("")

    # ── Extract enriched intelligence from leads ──
    enriched_leads = [
        l for l in leads
        if any("Sighting location extracted" in str(r) for r in (l.get("rationale") or []))
    ]

    # Collect all extracted intelligence
    all_sighting_locations = []
    all_mentioned_locations = set()
    all_physical_desc = []
    all_rewards = []
    all_contacts = []
    all_quotes = []
    all_clothing = []
    all_vehicles = []
    all_directions = []
    all_plates = []
    sighting_title_leads = []

    sighting_keywords = ["spotted", "sighted", "seen", "sighting", "possibly"]

    for lead in leads:
        rationale = lead.get("rationale") or []
        title = lead.get("title", "")

        # Check for sighting keywords in title
        if any(k in title.lower() for k in sighting_keywords):
            sighting_title_leads.append(lead)

        for r in rationale:
            rs = str(r)
            if rs.startswith("Sighting location extracted:"):
                loc = rs.replace("Sighting location extracted:", "").strip()
                all_sighting_locations.append({"location": loc, "lead": lead})
            if rs.startswith("Locations mentioned:"):
                locs = rs.replace("Locations mentioned:", "").strip().split(", ")
                all_mentioned_locations.update(locs)
            if rs.startswith("Physical description:"):
                desc = rs.replace("Physical description:", "").strip()
                all_physical_desc.append(desc)
            if rs.startswith("Reward:"):
                reward = rs.replace("Reward:", "").strip()
                all_rewards.append(reward)
            if rs.startswith("Contact:"):
                contact = rs.replace("Contact:", "").strip()
                all_contacts.append(contact)
            if rs.startswith("Quote:"):
                quote = rs.replace("Quote:", "").strip()
                all_quotes.append(quote)
            if rs.startswith("Clothing:"):
                clothing = rs.replace("Clothing:", "").strip()
                all_clothing.append(clothing)
            if rs.startswith("Vehicle:"):
                vehicle = rs.replace("Vehicle:", "").strip()
                all_vehicles.append(vehicle)
            if rs.startswith("Licence plate:"):
                plate = rs.replace("Licence plate:", "").strip()
                all_plates.append(plate)
            if rs.startswith("Direction:"):
                direction = rs.replace("Direction:", "").strip()
                all_directions.append(direction)

    # ── Verified Sighting Intelligence ──
    lines.append("## 2. SIGHTING INTELLIGENCE")

    # Deduplicate sighting locations: show each unique location once, with best source
    seen_locations: dict[str, list] = {}
    home_city_lower = case_city.lower().strip()
    for sl in all_sighting_locations:
        loc = sl["location"]
        # Skip the case's hometown — that's the origin, not a sighting
        if loc.lower().strip() == home_city_lower:
            continue
        if loc not in seen_locations:
            seen_locations[loc] = []
        seen_locations[loc].append(sl)

    if seen_locations:
        lines.append("")
        lines.append(f"**{len(seen_locations)} unique sighting location(s) extracted from news articles:**")
        lines.append("")
        for i, (loc, entries) in enumerate(seen_locations.items(), 1):
            # Use the highest-confidence lead as the primary source
            best = max(entries, key=lambda e: e["lead"].get("confidence", 0))
            lead = best["lead"]
            lines.append(f"### Sighting {i}: {loc}")
            lines.append(f"- **Source:** [{lead.get('title', '?')[:80]}]({lead.get('url', '#')})")
            lines.append(f"- **Confidence:** {_confidence_label(lead.get('confidence', 0))} ({lead.get('confidence', 0):.3f})")
            lines.append(f"- **Corroborated by:** {len(entries)} source(s)")
            if lead.get("published_at"):
                lines.append(f"- **Published:** {lead['published_at']}")
            lines.append("")
    else:
        lines.append("No sighting-location intelligence extracted from articles.")
        lines.append("")

    if sighting_title_leads:
        lines.append(f"### Leads with sighting keywords ({len(sighting_title_leads)})")
        lines.append("")
        for lead in sighting_title_leads[:10]:
            conf = lead.get("confidence", 0)
            lines.append(f"- **[{_confidence_label(conf)}]** [{lead.get('title', '?')[:100]}]({lead.get('url', '#')})")
        lines.append("")

    # ── Locations Grid ──
    if all_mentioned_locations:
        lines.append("## 3. LOCATIONS GRID")
        lines.append(f"All locations mentioned across {len(enriched_leads)} enriched articles:")
        lines.append("")
        for loc in sorted(all_mentioned_locations):
            marker = "🔴" if loc.lower() == case_city.lower() else "📍"
            lines.append(f"- {marker} **{loc}**")
        lines.append("")

    # ── Physical Description ──
    if all_physical_desc:
        lines.append("## 4. PHYSICAL DESCRIPTION (Extracted)")
        for desc in list(dict.fromkeys(all_physical_desc)):
            lines.append(f"- {desc}")
        lines.append("")

    if all_clothing:
        lines.append("### Clothing")
        for c in list(dict.fromkeys(all_clothing))[:5]:
            lines.append(f"- {c[:200]}")
        lines.append("")

    if all_vehicles or all_plates:
        lines.append("### Vehicle")
        for v in list(dict.fromkeys(all_vehicles)):
            lines.append(f"- {v}")
        if all_plates:
            for p in list(dict.fromkeys(all_plates)):
                lines.append(f"- **Licence plate:** {p}")
        lines.append("")

    if all_directions:
        lines.append("### Direction of Travel")
        for d in list(dict.fromkeys(all_directions)):
            lines.append(f"- {d}")
        lines.append("")

    # ── Witness Statements ──
    if all_quotes:
        lines.append("## 5. WITNESS STATEMENTS (Extracted)")
        for q in list(dict.fromkeys(all_quotes))[:8]:
            lines.append(f"> {q}")
            lines.append("")

    # ── Reward Information ──
    if all_rewards:
        lines.append("## 6. REWARD INFORMATION")
        for r in list(dict.fromkeys(all_rewards)):
            lines.append(f"- {r}")
        lines.append("")

    # ── Top Leads by Score ──
    lines.append("## 7. TOP LEADS BY CONFIDENCE")
    lines.append("")
    sorted_leads = sorted(leads, key=lambda l: l.get("confidence", 0), reverse=True)
    for i, lead in enumerate(sorted_leads[:20], 1):
        conf = lead.get("confidence", 0)
        ltype = lead.get("type", "?")
        cat = lead.get("category", "?")
        title = lead.get("title", "?")[:120]
        url = lead.get("url", "#")
        lines.append(f"{i}. **[{_confidence_label(conf)} {conf:.3f}]** [{title}]({url})")
        lines.append(f"   - Type: `{ltype}` | Category: `{cat}` | Source: {lead.get('source', '?')}")
        if lead.get("location"):
            lines.append(f"   - Location: {lead['location']}")
        lines.append("")

    # ── Hypothesis ──
    if hypothesis:
        lines.append("## 8. ANALYTICAL HYPOTHESIS")
        lines.append("")
        if hypothesis.get("primary_scenario"):
            lines.append(f"**Primary Scenario:** {hypothesis['primary_scenario']}")
            lines.append(f"**Confidence:** {hypothesis.get('primary_scenario_confidence', '?')}")
            lines.append("")

        if hypothesis.get("conclusion"):
            lines.append(f"### Conclusion")
            lines.append(hypothesis["conclusion"])
            lines.append("")

        if hypothesis.get("demographic_profile"):
            lines.append(f"**Demographic Profile:** {hypothesis['demographic_profile']}")
            lines.append("")

        if hypothesis.get("recommended_search_areas"):
            lines.append("### Recommended Search Areas")
            for area in hypothesis["recommended_search_areas"]:
                lines.append(f"- {area}")
            lines.append("")

        if hypothesis.get("critical_actions"):
            lines.append("### Critical Actions")
            for action in hypothesis["critical_actions"]:
                lines.append(f"- ⚡ {action}")
            lines.append("")

        if hypothesis.get("scenarios"):
            lines.append("### Scenario Analysis")
            for s in hypothesis["scenarios"]:
                lines.append(f"- **{s.get('name', '?')}** (weight: {s.get('weight', 0):.2f}, confidence: {s.get('confidence', '?')})")
                for ev in (s.get("evidence_for") or []):
                    lines.append(f"  - ✅ {ev}")
                for ev in (s.get("evidence_against") or []):
                    lines.append(f"  - ❌ {ev}")
            lines.append("")

    # ── Contact for Tips ──
    lines.append("## 9. CONTACT FOR TIPS")
    lines.append("")
    if case.get("authority"):
        lines.append(f"- **Investigating Authority:** {case['authority']}")
    if all_contacts:
        lines.append(f"- **Extracted contacts:** {', '.join(list(dict.fromkeys(all_contacts))[:5])}")

    # Find tip-line leads
    tip_leads = [l for l in leads if l.get("type") == "tip-line"]
    if tip_leads:
        lines.append("")
        lines.append("### Tip Lines")
        for tl in tip_leads:
            lines.append(f"- [{tl.get('title', '?')}]({tl.get('url', '#')})")

    lines.append("")
    lines.append("---")
    lines.append(f"*Report generated by MAAT Intelligence Pipeline — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append(f"*Based on {len(run.get('connectors', []))} OSINT connectors and {len(leads)} leads.*")

    report_text = "\n".join(lines)

    # Write report
    if output_path is None:
        output_path = result_path.with_suffix(".md")
    else:
        output_path = Path(output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    return str(output_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.generate_intel_report <result.json> [output.md]")
        sys.exit(1)
    result = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else None
    path = generate_intel_report(result, output)
    print(f"Report generated: {path}")
