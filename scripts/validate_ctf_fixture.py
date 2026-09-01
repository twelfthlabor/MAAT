"""Run a synthetic, offline CTF case through MAAT's locating gates."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.osint.hypothesis import generate_hypothesis
from backend.osint.lead_analysis import assess_lead
from backend.osint.location_evidence import evaluate_location_evidence
from backend.osint.synthesis import synthesize_investigation


DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_ctf_case.json"


def validate_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    case = payload["case"]
    expected = payload["expected"]
    leads = []
    for raw_lead in payload["leads"]:
        lead = dict(raw_lead)
        lead["analysis"] = assess_lead(lead)
        leads.append(lead)

    evidence = evaluate_location_evidence(
        leads,
        minimum_independent_sources=expected["minimum_independent_sources"],
    )
    missing_since = datetime.fromisoformat(case["missing_since"])
    synthesis = synthesize_investigation(
        case_id=case["id"],
        case_name=case["name"],
        leads=leads,
        missing_since=missing_since,
        case_lat=case["latitude"],
        case_lon=case["longitude"],
    )
    hypothesis = generate_hypothesis(
        case_id=case["id"],
        case_name=case["name"],
        case_age=case["age"],
        case_city=case["city"],
        case_province=case["province"],
        case_lat=case["latitude"],
        case_lon=case["longitude"],
        missing_since=missing_since,
        leads=leads,
    )

    best = evidence.get("best_candidate") or {}
    checks = {
        "location_evidence_sufficient": evidence["sufficient"],
        "expected_location_selected": best.get("location") == expected["location"],
        "independent_source_threshold_met": (
            best.get("independent_source_count", 0) >= expected["minimum_independent_sources"]
        ),
        "synthesis_has_corroborated_pattern": any(
            pattern.get("type") == "corroborated-location"
            for pattern in synthesis.geographic_patterns
        ),
        "hypothesis_uses_corroborated_location": expected["location"] in hypothesis.geographic_assessment.probable_zone,
        "distractors_rejected": evidence["rejected_count"] >= 3,
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "case_id": case["id"],
        "case_name": case["name"],
        "checks": checks,
        "location_evidence": evidence,
        "geographic_patterns": synthesis.geographic_patterns,
        "geographic_assessment": asdict(hypothesis.geographic_assessment),
        "flag": expected["flag"] if passed else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    result = validate_fixture(args.fixture)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
