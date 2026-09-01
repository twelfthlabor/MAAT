import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.case import Case, CasePhoto
from backend.models.investigation import InvestigationRun, Lead, ReviewDecision, SearchQueryLog
from backend.osint.connectors.base import ConnectorMetadata
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead
from backend.services.investigation_service import InvestigationService


class PhotoEchoConnector:
    metadata = ConnectorMetadata(
        name="photo-echo",
        source_kind="clear-web",
        disabled_by_default=False,
        description="Test connector that echoes the case photo URL.",
    )

    def enabled(self) -> bool:
        return True

    async def run(self, context):
        assert context.image_urls == ["https://example.org/case-photo.jpg"]
        assert context.location_text == "Wynford Dr & Concorde Pl, Toronto, ON"
        assert context.authority_name == "Toronto Police Service"
        assert context.authority_case_url == "https://example.org/toronto-police-case"
        assert context.case_reference_url and "objectid%3D1" in context.case_reference_url
        assert "https://example.org/toronto-police-case" in context.source_urls
        return ConnectorRunResult(
            leads=[
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind=self.metadata.source_kind,
                    lead_type="reverse-image-match",
                    category="reverse-image",
                    source_name="Photo Echo",
                    source_url="https://public.example.org/match",
                    query_used=context.image_urls[0],
                    found_at=datetime.now(timezone.utc),
                    title="Public photo match",
                    summary="Test lead created from a case photo.",
                    source_trust=0.55,
                    rationale=["Case photo was passed into the connector context."],
                )
            ],
            query_logs=[
                {
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": context.image_urls[0],
                    "status": "completed",
                    "http_status": 200,
                    "result_count": 1,
                }
            ],
        )


def test_investigation_service_passes_case_photo_urls(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr("backend.services.investigation_service.enabled_connectors", lambda: [PhotoEchoConnector()])

    with Session() as session:
        case = Case(
            id=1,
            slug="sample-case",
            name="Sample Case Toronto",
            aliases=[],
            city="Toronto",
            province="Ontario",
            age=14,
            status="missing",
            case_status="open",
            authority_name="Toronto Police Service",
            authority_case_url="https://example.org/toronto-police-case",
            official_summary_html=(
                "<b>Missing Since: </b> March 28, 2026\n"
                "<b>Location: </b> Wynford Dr & Concorde Pl, Toronto, ON\n"
                "<b>Last Seen Wearing: </b> black cropped jacket"
            ),
            source_feed="MCSC",
            is_active=True,
        )
        case.photos.append(CasePhoto(url="https://example.org/case-photo.jpg", thumb_url="https://example.org/case-photo.jpg", is_primary=True))
        session.add(case)
        session.commit()

        run = asyncio.run(InvestigationService(session).run_for_case(case.id))

        assert run.status == "completed"
        assert len(run.leads) == 1
        assert run.leads[0].query_used == "https://example.org/case-photo.jpg"
        assert "deduplicated lead" in (run.inference_summary or "")


def test_investigation_service_reuses_active_queued_run(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("backend.services.investigation_service.enabled_connectors", lambda: [PhotoEchoConnector()])

    with Session() as session:
        case = Case(
            id=2,
            slug="queued-case",
            name="Queued Example",
            aliases=[],
            city="Toronto",
            province="Ontario",
            age=15,
            status="missing",
            case_status="open",
            source_feed="MCSC",
            is_active=True,
        )
        session.add(case)
        session.commit()

        first, created = InvestigationService(session).queue_for_case(case.id)
        second, second_created = InvestigationService(session).queue_for_case(case.id)

        assert created is True
        assert second_created is False
        assert second.id == first.id
        assert first.status == "queued"
