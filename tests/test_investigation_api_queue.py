from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.app import create_app
from backend.core.database import Base, get_db
from backend.models.case import Case
from backend.models.investigation import InvestigationRun


def test_investigation_post_returns_queued_run(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    Base.metadata.create_all(bind=engine)
    with Session() as session:
        session.add(Case(
            id=44,
            slug="queued-api-case",
            name="Queued API Example",
            aliases=[],
            city="Toronto",
            province="Ontario",
            status="missing",
            case_status="open",
            source_feed="MCSC",
            is_active=True,
        ))
        session.commit()

    monkeypatch.setattr("backend.services.investigation_service.enabled_connectors", lambda: [])

    executed = []

    async def fake_execute(case_id: int, run_id: int):
        executed.append((case_id, run_id))

    monkeypatch.setattr("backend.api.investigations._execute_queued_investigation", fake_execute)

    app = create_app()

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    response = TestClient(app).post("/api/investigations/44")

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["reused_active_run"] is False
    assert executed == [(44, payload["run_id"])]
    with Session() as session:
        assert session.get(InvestigationRun, payload["run_id"]).status == "queued"
