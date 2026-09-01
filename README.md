# MAAT

MAAT is a public-source intelligence console for Canadian missing-person cases. It combines official case data with attributable news, archive, profile, and geospatial leads. Every result is a lead for human review—not proof of identity or location.

Live dashboard: [maat-cse-lover.vercel.app](https://maat-cse-lover.vercel.app/)

## What it does

- Syncs public MCSC ArcGIS case data.
- Searches official sources, Canadian news, GDELT, Reddit, Wayback Machine, Arquivo.pt, and public profile indexes.
- Uses Sherlock and WhatsMyName for bounded username pivots from discovered public profile URLs.
- Scores, deduplicates, enriches, maps, and synthesizes source-linked leads.
- Narrows locations only when independent source domains converge and at least one report is reviewed.
- Produces review queues, timelines, clusters, and authority-ready briefs.

## Safety

- Uses lawful, public, non-authenticated sources only.
- Does not contact subjects, relatives, witnesses, or private accounts.
- Does not treat username matches, archive captures, or sightings as confirmed identity or current location.
- Keep credentials in environment variables; never commit `.env`, databases, logs, exports, or raw private data.

## Architecture

```text
Vercel static dashboard  ->  Render FastAPI API/worker  ->  public OSINT sources
                                      |
                                   SQLite (local/demo)
```

The dashboard is static and hosted on Vercel. `vercel.json` proxies `/api/*` and `/healthz` to the FastAPI service. The backend runs long-lived Python connectors and local tools such as Sherlock.

## Backend speed

Investigation requests are asynchronous:

1. `POST /api/investigations/{case_id}` creates a run and returns HTTP `202` with status `queued` immediately (normally milliseconds).
2. The dashboard polls the run every 2.5 seconds while connectors work in the background.
3. Discovery connectors run concurrently (controlled by `CONNECTOR_CONCURRENCY`, default `4`). Sherlock and WhatsMyName run afterward so they can use handles found during discovery.
4. Each connector has a timeout. A slow archive or profile site becomes a warning instead of blocking the browser request.

The full sweep can still take seconds to several minutes because it depends on public APIs, rate limits, and Render cold starts. Render Free services may take about a minute to wake and have an ephemeral filesystem, so local SQLite history is not durable there. Use external Postgres for persistent production history.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
copy .env.example .env
python -m scripts.sync_cases
python -m backend.main
```

Serve `docs/` on port 8080 and open `http://localhost:8080`; the dashboard detects the local API on port 8000.

## Deploy

Frontend:

```bash
vercel --prod --yes
```

Backend: connect the repository to Render using `render.yaml`, set secrets in the Render environment, and deploy the `maat-backend` service. Enable investigator and public-source flags only for an authorized deployment. Render Free is suitable for a demo; use durable Postgres and a worker-capable plan for reliable history and long sweeps.

## Configuration

Copy `.env.example` to `.env`. Important flags are `ENABLE_INVESTIGATOR_MODE`, `ENABLE_CLEAR_WEB_CONNECTORS`, `ENABLE_PUBLIC_PROFILE_CHECKS`, `ENABLE_REVERSE_IMAGE_HOOKS`, and `CONNECTOR_CONCURRENCY`. Sherlock uses `SHERLOCK_BINARY` if the executable is not on `PATH`.

## Useful commands

```bash
python -m scripts.sync_cases
python -m scripts.export_public_data
python -m scripts.investigate_case <case_id>
python -m scripts.generate_intel_report <case_id>
python -m scripts.validate_ctf_fixture
python -m pytest -q
```

The test suite covers normalization, scoring, enrichment, synthesis, connector parsing, archive/profile pivots, and queued API behavior.
