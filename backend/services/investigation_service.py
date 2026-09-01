"""Optional investigator-mode orchestration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.enrichment.lead_enrichment import enrich_location_leads
from backend.enrichment.official_context import extract_official_context
from backend.models.case import Case
from backend.models.investigation import InvestigationRun, Lead, SearchQueryLog
from backend.osint.aggregation import merge_normalized_leads
from backend.osint.connectors.registry import enabled_connectors
from backend.osint.lead_analysis import assess_lead
from backend.osint.normalization.models import QueryContext
from backend.osint.pivots import discover_usernames
from backend.osint.scoring.lead_scoring import score_lead


class InvestigationService:
    """Runs configured connectors and persists normalized leads."""

    def __init__(self, db: Session):
        self.db = db

    def queue_for_case(self, case_id: int) -> tuple[InvestigationRun, bool]:
        """Create a durable queued run, or return the active run for this case."""

        case = self.db.get(Case, case_id)
        if case is None:
            raise ValueError(f"Case {case_id} not found.")

        active = (
            self.db.query(InvestigationRun)
            .filter(
                InvestigationRun.case_id == case_id,
                InvestigationRun.status.in_(("queued", "running")),
            )
            .order_by(InvestigationRun.started_at.desc())
            .first()
        )
        if active is not None:
            return active, False

        connectors = enabled_connectors()
        run = InvestigationRun(
            case_id=case.id,
            status="queued",
            connector_names=[connector.metadata.name for connector in connectors],
            feature_flags=settings.feature_flags,
            facts_summary="Official facts from MCSC/public police resources only.",
            inference_summary="Queued for lawful public-source collection.",
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run, True

    async def run_for_case(self, case_id: int, run_id: int | None = None) -> InvestigationRun:
        """Run all enabled connectors for a case."""
        case = self.db.get(Case, case_id)
        if case is None:
            raise ValueError(f"Case {case_id} not found.")

        connectors = enabled_connectors()
        run = self.db.get(InvestigationRun, run_id) if run_id is not None else None
        if run_id is not None and (run is None or run.case_id != case_id):
            raise ValueError(f"Queued investigation run {run_id} not found for case {case_id}.")
        if run is None:
            run = InvestigationRun(case_id=case.id)
            self.db.add(run)

        run.status = "running"
        run.connector_names = [connector.metadata.name for connector in connectors]
        run.feature_flags = settings.feature_flags
        run.facts_summary = "Official facts from MCSC/public police resources only."
        run.inference_summary = "Leads below are unverified and require analyst review before any action."
        self.db.commit()
        self.db.refresh(run)

        official_context = extract_official_context(
            case.official_summary_html,
            city=case.city,
            province=case.province,
        )
        query_context = QueryContext(
            case_id=case.id,
            name=case.name or "",
            aliases=case.aliases or [],
            city=case.city,
            province=case.province,
            age=case.age,
            missing_since=case.missing_since,
            location_text=official_context.get("location_text"),
            authority_name=case.authority_name,
            authority_case_url=case.authority_case_url,
            case_reference_url=(
                f"{settings.mcsc_feature_server_url}/query?where=objectid%3D{case.id}&outFields=*"
                "&returnGeometry=true&f=json"
            ),
            source_urls=[
                value
                for value in [case.authority_case_url, case.source_url, *(record.source_url for record in case.source_records)]
                if value
            ],
            image_urls=[photo.url for photo in case.photos if photo.url],
        )

        collected_leads = []
        connector_failures = []

        try:
            semaphore = asyncio.Semaphore(max(1, settings.connector_concurrency))

            async def invoke_connector(connector):
                connector_timeout = (
                    connector.metadata.timeout_seconds
                    or settings.connector_timeout_seconds
                )
                try:
                    async with semaphore:
                        result = await asyncio.wait_for(
                            connector.run(query_context),
                            timeout=connector_timeout,
                        )
                    return connector, result, None, connector_timeout
                except asyncio.TimeoutError:
                    return connector, None, "timeout", connector_timeout
                except Exception as exc:
                    return connector, None, exc, connector_timeout

            async def run_phase(phase_connectors):
                outcomes = await asyncio.gather(
                    *(invoke_connector(connector) for connector in phase_connectors)
                )
                for connector, result, error, connector_timeout in outcomes:
                    if error == "timeout":
                        connector_failures.append(f"{connector.metadata.name}: timed out")
                        run.query_logs.append(
                            SearchQueryLog(
                                connector_name=connector.metadata.name,
                                source_kind=connector.metadata.source_kind,
                                query_used="[connector invocation]",
                                status="timeout",
                                notes=f"Exceeded {connector_timeout:g}s connector timeout",
                                completed_at=datetime.now(timezone.utc),
                            )
                        )
                        continue
                    if error is not None:
                        connector_failures.append(f"{connector.metadata.name}: {error}")
                        run.query_logs.append(
                            SearchQueryLog(
                                connector_name=connector.metadata.name,
                                source_kind=connector.metadata.source_kind,
                                query_used="[connector invocation]",
                                status="failed",
                                notes=str(error),
                                completed_at=datetime.now(timezone.utc),
                            )
                        )
                        continue

                    if result.warning:
                        run.query_logs.append(
                            SearchQueryLog(
                                connector_name=connector.metadata.name,
                                source_kind=connector.metadata.source_kind,
                                query_used="[connector warning]",
                                status="warning",
                                notes=result.warning,
                                completed_at=datetime.now(timezone.utc),
                            )
                        )

                    for query_log in result.query_logs:
                        run.query_logs.append(
                            SearchQueryLog(
                                connector_name=query_log["connector_name"],
                                source_kind=query_log["source_kind"],
                                query_used=query_log["query_used"],
                                status=query_log.get("status", "completed"),
                                http_status=query_log.get("http_status"),
                                result_count=query_log.get("result_count", 0),
                                notes=query_log.get("notes"),
                                completed_at=datetime.now(timezone.utc),
                            )
                        )

                    collected_leads.extend(result.leads)
                    known_usernames = {item.lower() for item in query_context.usernames}
                    for username in discover_usernames(result.leads):
                        if username.lower() not in known_usernames:
                            query_context.usernames.append(username)
                            known_usernames.add(username.lower())

            pivot_names = {"sherlock", "whatsmyname"}
            discovery_connectors = [
                connector for connector in connectors
                if connector.metadata.name not in pivot_names
            ]
            pivot_connectors = [
                connector for connector in connectors
                if connector.metadata.name in pivot_names
            ]
            await run_phase(discovery_connectors)
            await run_phase(pivot_connectors)

            normalized_leads = merge_normalized_leads(collected_leads)
            enriched_count = await enrich_location_leads(case, normalized_leads)
            location_candidates = sum(
                1 for normalized in normalized_leads if assess_lead(normalized)["is_location_candidate"]
            )
            run.query_logs.append(
                SearchQueryLog(
                    connector_name="content-enrichment",
                    source_kind="public-article",
                    query_used=f"Top {min(len(normalized_leads), settings.max_enrichment_articles)} relevant article leads",
                    status="completed",
                    result_count=enriched_count,
                    notes="Fetched public articles, validated subject context, extracted locating details, and geocoded public places.",
                    completed_at=datetime.now(timezone.utc),
                )
            )
            for normalized in normalized_leads:
                scored = score_lead(case, normalized)
                assessment = assess_lead(normalized)
                run.leads.append(
                    Lead(
                        case_id=case.id,
                        lead_type=normalized.lead_type,
                        category=normalized.category,
                        source_kind=normalized.source_kind,
                        source_name=normalized.source_name,
                        source_url=normalized.source_url,
                        query_used=normalized.query_used,
                        title=normalized.title,
                        summary=normalized.summary,
                        content_excerpt=normalized.content_excerpt,
                        published_at=normalized.published_at,
                        found_at=normalized.found_at,
                        location_text=normalized.location_text,
                        latitude=normalized.latitude,
                        longitude=normalized.longitude,
                        confidence=scored.score,
                        source_trust=normalized.source_trust,
                        corroboration_count=normalized.corroboration_count,
                        rationale=scored.rationale,
                        human_reason=assessment["next_step"],
                    )
                )

            if connector_failures and normalized_leads:
                run.status = "completed_with_warnings"
            elif connector_failures and not normalized_leads:
                run.status = "failed"
            else:
                run.status = "completed"

            run.inference_summary = (
                f"{len(normalized_leads)} deduplicated lead(s); {enriched_count} article(s) enriched; "
                f"{location_candidates} unverified location candidate(s). "
                "Relevance and locating value are scored separately. Public-source candidates require analyst review."
            )
            if connector_failures:
                run.error_message = " | ".join(connector_failures)

            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(run)
            return run
        except Exception as exc:
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            raise
