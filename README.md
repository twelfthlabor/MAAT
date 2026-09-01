# MAAT — Truth from Chaos

OSINT intelligence platform for Canadian missing children cases. Named after Ma'at, the Egyptian concept of truth and order — because finding missing children means extracting truth from chaos.

**The end goal: find the missing kids and notify authorities so we can actually help.**

The previous GitHub Pages URL is no longer active. This repository now includes
`vercel.json` for a free Vercel frontend deployment with same-origin proxying to
the existing Render investigation worker.

## What MAAT Does

1. **Ingests** public case data from the Missing Children Society of Canada (MCSC) ArcGIS feed
2. **Sweeps** multiple OSINT connectors — news outlets, web archives, social platforms, official registries — for leads on each case
3. **Scores** every lead with transparent confidence ratings, source attribution, and rationale
4. **Clusters** leads thematically — sighting reports, media coverage, official updates — by similarity and geography
5. **Detects patterns** — geographic clusters, temporal bursts, cold trails
6. **Synthesizes** actionable intelligence: situation summaries, authority briefs, and prioritized recommendations
7. **Routes** discoveries to the listed investigating authority or MCSC for follow-up

## Safety & Ethics

- Official facts and inferred context are always separated.
- Only lawful, public, non-authenticated sources are used. No scraping behind logins.
- No doxxing, no contacting relatives, no vigilante action.
- Every lead is intended to be reported to the listed authority or the Missing Children Society of Canada.
- MAAT generates intelligence — humans and authorities make decisions.

## Architecture

```text
docs/            Static public dashboard (Vercel or GitHub Pages)
backend/         FastAPI backend — ingestion, enrichment, OSINT connectors, synthesis
  api/           REST endpoints (cases, exports, investigations, sync)
  core/          Config, database, scheduler
  ingestion/     MCSC ArcGIS feed parser
  enrichment/    Geospatial, timeline, official context, resource layers
  osint/         Connector framework, scoring, normalization, synthesis engine
  models/        SQLAlchemy models (Case, Investigation)
  services/      Business logic (case, investigation, export, review)
shared/          Shared schemas, constants, and utilities
scripts/         CLI entrypoints (sync, export, build, investigate)
data/            Local cache, exports, reference layers (not tracked)
tests/           Pytest suite
```

## Quick Start

### Static dashboard only

Open `docs/index.html` in a browser, or deploy the `docs/` folder to GitHub Pages.

The bundled `docs/data/public-cases.json` provides an offline preview. Use the in-browser live-source toggle to fetch directly from the MCSC ArcGIS feed.

### Backend / investigator mode

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env

python -m scripts.sync_cases          # pull open cases into SQLite
python -m scripts.export_public_data  # write JSON/CSV exports
python -m backend.main                # start FastAPI server
```

### Deploy the frontend to Vercel (recommended)

The FastAPI service runs scheduled jobs, writes SQLite data, and can execute
long-running local tools such as Sherlock. Those jobs are not a good fit for an
ephemeral Vercel Function, so the free deployment is intentionally hybrid:

- **Vercel:** static dashboard, bundled fallback data, and same-origin API proxy
- **Render:** FastAPI investigation worker and OSINT connectors

Import this repository in Vercel and deploy from the repository root. No build
command, output directory, or frontend environment variables are required;
`vercel.json` serves `docs/dashboard.html` at `/` and proxies `/api/*` plus
`/healthz` to the Render service.

For local development, serve `docs/` on port 8080 and run FastAPI on port 8000.
The dashboard detects localhost automatically.

### Deploy to GitHub Pages (static fallback)

1. Push the repo to GitHub.
2. Repository Settings → Pages → Deploy from branch `main`, folder `/docs`.
3. The public site needs no secrets or backend.

To refresh the static dataset:

```bash
python -m scripts.sync_cases
python -m scripts.build_docs
```

Then commit the updated `docs/data/` files.

## OSINT Connectors

All connectors are feature-flagged and disabled by default. Enable them via environment variables:

| Flag | Purpose |
|------|---------|
| `ENABLE_INVESTIGATOR_MODE` | Unlock investigation endpoints and OSINT sweeps |
| `ENABLE_CLEAR_WEB_CONNECTORS` | News, archive, and web search connectors |
| `ENABLE_PUBLIC_PROFILE_CHECKS` | Social media and public profile lookups |
| `ENABLE_REVERSE_IMAGE_HOOKS` | Reverse image search integration |
| `ENABLE_LOCAL_FACE_WORKFLOW` | Local face comparison workflow |
| `ENABLE_DARK_WEB_CONNECTORS` | Dark web index search (Ahmia) |
| `ENABLE_EXPERIMENTAL_CONNECTORS` | Experimental/in-progress connectors |

Active connectors include GDELT DOC 2.0 (news/timeline), SearXNG (multi-engine search), Canadian news media, Bing News, Google News RSS, Reddit, the Wayback Machine CDX API, Arquivo.pt full-text archive search, Sherlock, bounded live WhatsMyName checks, Canada Missing registry, and official artifacts. Additional adapters (SpiderFoot, theHarvester, Recon-ng, OnionSearch) exist as scaffolds for future integration.

Sherlock uses the upstream `sherlock-project` executable and writes its live
username sweep to a temporary CSV report that MAAT normalizes into explicitly
unverified account candidates. It never treats a username match as identity or
location proof. Set `SHERLOCK_BINARY` when the executable is not on `PATH`.

Investigation requests return a queued run immediately. The dashboard polls the
run record while the worker gathers source-linked results, so a slow archive or
profile method no longer turns the browser request into a false failure. Handles
found in recognized public-profile URLs are passed to later username tools; name
permutations remain lower-confidence fallback hypotheses.

## Intelligence Synthesis

After an investigation run, the synthesis engine produces:

- **Situation summary** — plain-language assessment of the intelligence landscape
- **Lead clusters** — thematic groups by similarity and geography
- **Intelligence timeline** — source-attributed chronological events
- **Geographic patterns** — location clusters, dispersal analysis, distance from case origin
- **Temporal patterns** — activity bursts, cold trail detection, recent activity windows
- **Actionable recommendations** — prioritized next steps (CRITICAL / HIGH / MEDIUM)
- **Authority brief** — ready-to-forward summary for investigating authorities

## Scripts

| Command | Description |
|---------|-------------|
| `python -m scripts.sync_cases` | Pull open cases from the MCSC ArcGIS feed into SQLite |
| `python -m scripts.export_public_data` | Write JSON/CSV exports |
| `python -m scripts.build_docs` | Regenerate static dashboard data files |
| `python -m scripts.investigate_case <case_id>` | Run OSINT connectors for a specific case |
| `python -m scripts.generate_intel_report <case_id>` | Generate an intelligence report |
| `python -m scripts.refresh_osint_cache <case_id>` | Refresh cached OSINT data for a case |

## Tests

```bash
pytest
```

Current tests cover:
- ArcGIS normalization
- lead scoring rationale
- timeline derivation
- investigator query planning
- resource-pack generation

## Dashboard Features

- Live case count with province, city, and age filters
- Fuzzy name search and sorting by recency, age, status, risk rank
- Interactive map with case pins and reference layers (borders, highways, transit, youth services)
- Case detail panel separating official facts from inferred context
- Source attribution badges and recently-updated panel
- Printable case packets and shareable filtered URLs
- Province, age, status, and trend charts
- Authority contact links and safe-help reporting checklists

