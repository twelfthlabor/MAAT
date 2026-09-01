/* ═══════════════════════════════════════════════════════════
   MAAT — Missing Persons Intelligence Console
   Main Application Module
   ═══════════════════════════════════════════════════════════ */

// ─── Configuration ───
const IS_LOCAL_HOST = ["localhost", "127.0.0.1"].includes(window.location.hostname);

const CONFIG = {
  // Vercel proxies the same-origin API to the long-running Render worker. Local
  // development still prefers the local FastAPI process.
  API_URLS: IS_LOCAL_HOST
    ? ["http://127.0.0.1:8000", "http://localhost:8000", "https://maat-backend-yzu6.onrender.com"]
    : [window.location.origin, "https://maat-backend-yzu6.onrender.com"],
  // ArcGIS direct feed for fallback
  ARCGIS_URL: "https://services.arcgis.com/Sv9ZXFjH5h1fYAaI/arcgis/rest/services/Missing_Children_Cases_View_Master/FeatureServer/0",
  // Bundled data fallback
  STATIC_URL: "./data/public-cases.json",
  // Map tiles
  TILE_URL: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
  TILE_ATTR: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
  // Canada center
  MAP_CENTER: [56.1, -96.0],
  MAP_ZOOM: 4,
};

const STATUS_LABELS = {
  missing: "Missing",
  vulnerable: "Vulnerable",
  abudction: "Abduction",
  abduction: "Abduction",
  amberalert: "Amber Alert",
  childsearchalert: "Child Search Alert",
};

const CONNECTORS = [
  { id: "official-artifacts", name: "Official Sources", icon: "🏛️" },
  { id: "canada-missing-xref", name: "Canada Missing XRef", icon: "🇨🇦" },
  { id: "canadian-news-media", name: "Canadian News (CBC/CTV/Global)", icon: "📺" },
  { id: "google-news-rss", name: "Google News RSS", icon: "📰" },
  { id: "bing-news-rss", name: "Bing News RSS", icon: "📡" },
  { id: "duckduckgo-html", name: "DuckDuckGo", icon: "🦆" },
  { id: "reddit-search", name: "Reddit Search", icon: "💬" },
  { id: "wayback-machine", name: "Wayback Machine", icon: "🕰️" },
  { id: "arquivo-pt", name: "Arquivo.pt Archive", icon: "📚" },
  { id: "sherlock", name: "Sherlock Username Sweep", icon: "🕵️" },
  { id: "whatsmyname", name: "WhatsMyName Fingerprints", icon: "🪪" },
  { id: "gdelt-doc", name: "GDELT Doc API", icon: "🌐" },
  { id: "reverse-image-hook", name: "Reverse Image Search", icon: "🔍" },
  { id: "social-profiler", name: "Social Media Profiler", icon: "👤" },
  { id: "network-analysis", name: "Network Analysis", icon: "🕸️" },
  { id: "ahmia", name: "Ahmia Index", icon: "🧅" },
  { id: "geospatial", name: "Geospatial Analysis", icon: "🗺️" },
];

// ─── State ───
const state = {
  apiBase: null,
  apiOnline: false,
  cases: [],
  filteredCases: [],
  selectedCaseId: null,
  selectedCase: null,
  selectedRunId: null,
  runs: [],
  leads: [],
  queryLogs: [],
  synthesis: null,
  view: "overview", // "overview" | "dossier"
  maps: {},
};

// ─── DOM References ───
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

function safeHttpUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

function setMobileCasesOpen(open) {
  $("#caseNav")?.classList.toggle("is-open", open);
  $("#caseNavScrim")?.classList.toggle("is-open", open);
  $("#mobileCasesBtn")?.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("has-mobile-nav", open);
}

// ═══════════════════════════════════════════════════════════
// API LAYER
// ═══════════════════════════════════════════════════════════

async function detectApi() {
  // Try local backends first (fast timeout), then remote (longer for cold starts)
  for (const url of CONFIG.API_URLS) {
    const isRemote = url.startsWith("https://");
    const timeout = isRemote ? 60000 : 3000;
    try {
      if (isRemote) {
        showToast("Connecting to hosted backend… (may take a moment on first load)", "info");
      }
      const res = await fetch(`${url}/healthz`, { signal: AbortSignal.timeout(timeout) });
      if (res.ok) {
        state.apiBase = url;
        state.apiOnline = true;
        return url;
      }
    } catch { /* try next */ }
  }
  state.apiOnline = false;
  return null;
}

async function api(path, opts = {}) {
  if (!state.apiBase) return null;
  try {
    const res = await fetch(`${state.apiBase}${path}`, {
      ...opts,
      headers: { "Content-Type": "application/json", ...opts.headers },
    });
    if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.error(`API error: ${path}`, err);
    return null;
  }
}

// ─── Load cases from API, ArcGIS, or static fallback ───
async function loadCases() {
  // Try API first
  if (state.apiOnline) {
    const data = await api("/api/cases");
    if (data?.cases?.length) {
      return data.cases.map((c) => ({ ...c, _source: "api" }));
    }
  }

  // Try ArcGIS direct
  try {
    const url = `${CONFIG.ARCGIS_URL}/query?where=1%3D1&outFields=*&f=json&resultRecordCount=500`;
    const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
    const data = await res.json();
    if (data?.features?.length) {
      return data.features.map((f) => normalizeArcgis(f));
    }
  } catch { /* fall through */ }

  // Static fallback
  try {
    const res = await fetch(CONFIG.STATIC_URL);
    const data = await res.json();
    return (data.cases || []).map((c) => ({ ...c, _source: "static" }));
  } catch {
    return [];
  }
}

function normalizeArcgis(feature) {
  const a = feature.attributes || {};
  const missingSince = a.Date_Missing ? new Date(a.Date_Missing).toISOString() : null;
  return {
    id: a.OBJECTID || a.FID || Math.random(),
    name: [a.First_Name, a.Last_Name].filter(Boolean).join(" ") || a.First_Name || "Unknown",
    province: a.Province || "",
    city: a.City || "",
    status: (a.Status || "missing").toLowerCase().replace(/\s+/g, ""),
    age: a.Age != null ? Number(a.Age) : null,
    gender: a.Gender || null,
    missing_since: missingSince,
    latitude: a.Latitude || feature.geometry?.y || null,
    longitude: a.Longitude || feature.geometry?.x || null,
    photo_url: a.Photo_URL || a.PhotoURL || null,
    authority_name: a.Authority || a.Police_Agency || null,
    authority_phone: a.Authority_Phone || null,
    _source: "arcgis",
  };
}

// ═══════════════════════════════════════════════════════════
// UI RENDERING
// ═══════════════════════════════════════════════════════════

// ─── Case List ───
function renderCaseList() {
  const list = $("#caseList");
  if (!state.filteredCases.length) {
    list.innerHTML = '<div class="empty-state"><p>No cases match your filters.</p></div>';
    return;
  }

  list.innerHTML = state.filteredCases.map((c, i) => {
    const isSelected = c.id === state.selectedCaseId;
    const badge = statusBadge(c.status);
    const elapsed = c.missing_since ? elapsedText(c.missing_since) : "";
    const photoUrl = safeHttpUrl(c.photo_url);
    const photoHtml = photoUrl
      ? `<img class="case-card__photo" src="${escHtml(photoUrl)}" alt="${escHtml(c.name)}" loading="lazy">`
      : `<div class="case-card__photo-placeholder">👤</div>`;

    return `
      <div class="case-card${isSelected ? " is-selected" : ""} animate-in"
           data-case-id="${c.id}" style="animation-delay:${Math.min(i * 20, 400)}ms">
        ${photoHtml}
        <div class="case-card__body">
          <div class="case-card__name">${escHtml(c.name)}</div>
          <div class="case-card__detail">
            <span class="case-card__location">${escHtml(c.city || "—")}${c.province ? ", " + escHtml(c.province) : ""}</span>
            ${badge}
          </div>
          ${elapsed ? `<div class="case-card__elapsed">${elapsed}</div>` : ""}
        </div>
      </div>`;
  }).join("");

  // Click handlers
  list.querySelectorAll(".case-card").forEach((card) => {
    card.addEventListener("click", () => {
      const id = Number(card.dataset.caseId);
      selectCase(id);
    });
  });
  list.querySelectorAll("img.case-card__photo").forEach((img) => {
    img.addEventListener("error", () => {
      const fallback = document.createElement("div");
      fallback.className = "case-card__photo-placeholder";
      fallback.textContent = "👤";
      img.replaceWith(fallback);
    }, { once: true });
  });
}

function statusBadge(status) {
  const s = (status || "").toLowerCase().replace(/\s+/g, "");
  const cls = {
    missing: "missing", vulnerable: "vulnerable",
    abduction: "abduction", abudction: "abduction",
    amberalert: "amberalert",
  }[s] || "default";
  const label = STATUS_LABELS[s] || status || "Unknown";
  return `<span class="case-card__badge case-card__badge--${cls}">${escHtml(label)}</span>`;
}

// ─── Overview Stats ───
function renderOverviewStats() {
  const cases = state.cases;
  const provinces = new Set(cases.map((c) => c.province).filter(Boolean));
  
  $("#statTotalCases").textContent = cases.length;
  $("#statProvinces").textContent = provinces.size;
  
  // Count investigated (requires API)
  if (state.apiOnline) {
    api("/api/cases/stats").then((data) => {
      if (data) {
        $("#statInvestigated").textContent = data.investigated_count || "—";
        $("#statTotalLeads").textContent = data.total_leads || "—";
      }
    });
  } else {
    $("#statInvestigated").textContent = "—";
    $("#statTotalLeads").textContent = "—";
  }
}

// ─── Province Chart ───
function renderProvinceChart() {
  const counts = {};
  state.cases.forEach((c) => {
    const p = c.province || "Unknown";
    counts[p] = (counts[p] || 0) + 1;
  });

  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = sorted[0]?.[1] || 1;

  const container = $("#provinceChart");
  container.innerHTML = sorted.slice(0, 10).map(([prov, count]) => {
    const pct = Math.round((count / max) * 100);
    return `
      <div class="province-bar">
        <span class="province-bar__label">${escHtml(prov)}</span>
        <div class="province-bar__track">
          <div class="province-bar__fill" style="width:${pct}%"></div>
        </div>
        <span class="province-bar__count">${count}</span>
      </div>`;
  }).join("");
}

// ─── Priority Cases ───
function renderPriorityCases() {
  // Priority = most recent + highest risk status
  const prioritized = [...state.cases]
    .filter((c) => c.missing_since)
    .sort((a, b) => {
      const statusWeight = { amberalert: 4, abduction: 3, abudction: 3, vulnerable: 2, missing: 1 };
      const wa = statusWeight[(a.status || "").toLowerCase()] || 0;
      const wb = statusWeight[(b.status || "").toLowerCase()] || 0;
      if (wb !== wa) return wb - wa;
      return new Date(b.missing_since) - new Date(a.missing_since);
    })
    .slice(0, 5);

  const container = $("#priorityCases");
  if (!prioritized.length) {
    container.innerHTML = '<p class="muted-text">No cases with dates available.</p>';
    return;
  }

  container.innerHTML = prioritized.map((c, i) => {
    const days = daysSince(c.missing_since);
    return `
      <div class="priority-card" data-case-id="${c.id}">
        <span class="priority-card__rank">${i + 1}</span>
        <div class="priority-card__info">
          <div class="priority-card__name">${escHtml(c.name)}</div>
          <div class="priority-card__meta">${escHtml(c.city || "")}${c.province ? ", " + escHtml(c.province) : ""}</div>
        </div>
        <span class="priority-card__days">${days}d</span>
      </div>`;
  }).join("");

  container.querySelectorAll(".priority-card").forEach((card) => {
    card.addEventListener("click", () => selectCase(Number(card.dataset.caseId)));
  });
}

// ─── Connector Health ───
function renderConnectorGrid() {
  const container = $("#connectorGrid");
  container.innerHTML = CONNECTORS.map((c) => `
    <div class="connector-card">
      <span class="connector-card__dot connector-card__dot--ok"></span>
      <span class="connector-card__name">${c.icon} ${c.name}</span>
      <span class="connector-card__status">Ready</span>
    </div>`).join("");
}

// ─── Connector status dots in footer ───
function renderConnectorDots() {
  const container = $("#connectorDots");
  container.innerHTML = CONNECTORS.map((c) =>
    `<span class="connector-dot connector-dot--ok" title="${c.name}"></span>`
  ).join("");
}

// ═══════════════════════════════════════════════════════════
// CASE DOSSIER
// ═══════════════════════════════════════════════════════════

async function selectCase(caseId) {
  setMobileCasesOpen(false);
  state.selectedCaseId = caseId;
  state.selectedCase = state.cases.find((c) => c.id === caseId);
  state.selectedRunId = null;

  // Highlight in list
  $$(".case-card").forEach((el) => el.classList.toggle("is-selected", Number(el.dataset.caseId) === caseId));

  // Switch view
  showView("dossier");
  // Establish the initial tab before any network work begins. Previously this
  // happened after asynchronous requests and could override a tab the analyst
  // had already selected, most visibly on mobile.
  switchTab("facts");

  // Render dossier header
  renderDossierHeader();

  // Load investigation data if API online
  if (state.apiOnline) {
    const [fullCase] = await Promise.all([
      api(`/api/cases/${caseId}`),
      loadCaseRuns(caseId),
    ]);
    if (fullCase) {
      state.selectedCase = { ...state.selectedCase, ...fullCase };
    }
  }

  // Render facts
  renderFacts();
  
  // Update intel panel map
  renderIntelMap();

  // Update authority quick action
  updateQuickActions();
  
}

function renderDossierHeader() {
  const c = state.selectedCase;
  if (!c) return;

  // Photo
  const photoEl = $("#dossierPhoto");
  const photoUrl = safeHttpUrl(c.photo_url);
  photoEl.innerHTML = photoUrl
    ? `<img src="${escHtml(photoUrl)}" alt="${escHtml(c.name)}">`
    : '<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:24px;color:var(--text-muted)">👤</div>';
  photoEl.querySelector("img")?.addEventListener("error", () => {
    photoEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:24px;color:var(--text-muted)">👤</div>';
  }, { once: true });

  // Status badge
  const statusEl = $("#dossierStatus");
  const s = (c.status || "missing").toLowerCase();
  const colors = {
    missing: ["var(--amber-dim)", "var(--amber)"],
    vulnerable: ["var(--red-dim)", "var(--red)"],
    abduction: ["var(--red-dim)", "var(--red-bright)"],
    abudction: ["var(--red-dim)", "var(--red-bright)"],
    amberalert: ["rgba(248,113,113,0.25)", "#fff"],
  };
  const [bg, fg] = colors[s] || ["var(--blue-dim)", "var(--blue)"];
  statusEl.style.background = bg;
  statusEl.style.color = fg;
  statusEl.textContent = STATUS_LABELS[s] || c.status || "Unknown";

  // Name & location
  $("#dossierName").textContent = c.name || "Unknown";
  const locationParts = [c.city, c.province].filter(Boolean);
  const location = locationParts.length ? locationParts.join(", ") : "Location unknown";
  const dateStr = c.missing_since ? `Missing since ${formatDate(c.missing_since)}` : "";
  $("#dossierLocation").textContent = [location, dateStr].filter(Boolean).join(" · ");
}

function renderFacts() {
  const c = state.selectedCase;
  if (!c) return;
  const grid = $("#factsGrid");
  
  const facts = [];

  // Official facts
  if (c.name) facts.push({ label: "Full Name", value: c.name, type: "official" });
  if (c.age != null) facts.push({ label: "Age at Disappearance", value: String(c.age), type: "official" });
  if (c.gender) facts.push({ label: "Gender", value: c.gender, type: "official" });
  if (c.city) facts.push({ label: "City", value: c.city, type: "official" });
  if (c.province) facts.push({ label: "Province / Territory", value: c.province, type: "official" });
  if (c.missing_since) {
    const days = daysSince(c.missing_since);
    facts.push({
      label: "Missing Since",
      value: `${formatDate(c.missing_since)} (${days} days)`,
      type: "official",
    });
  }
  if (c.latitude && c.longitude) {
    facts.push({
      label: "Coordinates",
      value: `${Number(c.latitude).toFixed(4)}°N, ${Number(c.longitude).toFixed(4)}°W`,
      type: "official",
    });
  }

  // Contact
  if (c.authority_name) facts.push({ label: "Investigating Authority", value: c.authority_name, type: "contact" });
  if (c.authority_phone) facts.push({ label: "Authority Phone", value: c.authority_phone, type: "contact" });
  if (c.authority_email) facts.push({
    label: "Authority Email",
    value: `<a href="mailto:${escHtml(c.authority_email)}">${escHtml(c.authority_email)}</a>`,
    type: "contact",
    raw: true,
  });
  if (c.authority_case_url) facts.push({
    label: "Official Case Page",
    value: `<a href="${escHtml(c.authority_case_url)}" target="_blank" rel="noopener" title="${escHtml(c.authority_case_url)}">${escHtml(truncateUrl(c.authority_case_url))}</a>`,
    type: "contact",
    raw: true,
  });
  if (c.mcsc_phone) facts.push({ label: "MCSC Phone", value: c.mcsc_phone, type: "contact" });

  // Summary
  if (c.official_summary_html) {
    facts.push({ label: "Official Summary", value: sanitizeHtml(c.official_summary_html), type: "summary", raw: true });
  }

  grid.innerHTML = facts.map((f) => `
    <div class="fact-card fact-card--${f.type}">
      <div class="fact-card__label">${escHtml(f.label)}</div>
      <div class="fact-card__value">${f.raw ? f.value : escHtml(f.value)}</div>
    </div>`).join("");
}

// ─── Investigation Runs ───
async function loadCaseRuns(caseId) {
  const data = await api(`/api/investigations/cases/${caseId}/runs`);
  state.runs = data?.runs || [];
  renderRunHistory();

  // Auto-select latest run
  if (state.runs.length) {
    await selectRun(state.runs[0].id);
  } else {
    state.leads = [];
    state.queryLogs = [];
    state.synthesis = null;
    renderLeads();
    renderQueryLog();
    generateAssessment();
    $("#leadCountBadge").textContent = "0";
  }
}

function renderRunHistory() {
  const container = $("#runHistory");
  if (!state.runs.length) {
    container.innerHTML = '<p class="muted-text">No investigations yet. Click "Run Investigation" to start.</p>';
    return;
  }

  container.innerHTML = state.runs.map((r) => {
    const date = formatDateTime(r.started_at);
    const isSelected = r.id === state.selectedRunId;
    return `
      <div class="run-item${isSelected ? " is-selected" : ""}" data-run-id="${r.id}">
        <span class="run-item__id">Run #${r.id}</span>
        <span class="run-item__info">${date}</span>
        <span class="run-item__leads">${r.lead_count || 0} leads</span>
      </div>`;
  }).join("");

  container.querySelectorAll(".run-item").forEach((el) => {
    el.addEventListener("click", () => selectRun(Number(el.dataset.runId)));
  });
}

async function selectRun(runId) {
  state.selectedRunId = runId;

  // Highlight
  $$(".run-item").forEach((el) => el.classList.toggle("is-selected", Number(el.dataset.runId) === runId));

  // Load leads, query logs, and backend synthesis in parallel.
  const [leadsData, queryData, synthesisData] = await Promise.all([
    api(`/api/investigations/runs/${runId}/leads?limit=200`),
    api(`/api/investigations/runs/${runId}/query-logs`),
    api(`/api/investigations/runs/${runId}/synthesis`),
  ]);

  state.leads = leadsData?.leads || [];
  state.queryLogs = queryData?.query_logs || [];
  state.synthesis = synthesisData || null;

  $("#leadCountBadge").textContent = String(state.leads.length);
  
  renderLeads();
  renderQueryLog();
  populateLeadSourceFilter();
  renderTimeline();
  generateAssessment();
}

// ─── Leads ───
function renderLeads() {
  const container = $("#leadsList");
  const minConf = parseFloat($("#leadFilterConfidence").value) || 0;
  const sourceFilter = $("#leadFilterSource").value;
  const reviewFilter = $("#leadFilterReview").value;

  let filtered = state.leads.filter((l) => l.confidence >= minConf);
  if (sourceFilter) filtered = filtered.filter((l) => l.source_name === sourceFilter);
  if (reviewFilter) filtered = filtered.filter((l) => l.review_status === reviewFilter);
  filtered.sort((a, b) => {
    const actionDelta = (b.analysis?.actionability_score || 0) - (a.analysis?.actionability_score || 0);
    return actionDelta || (b.confidence || 0) - (a.confidence || 0);
  });

  const summary = $("#leadsSummary");
  const candidateCount = filtered.filter((l) => l.analysis?.is_location_candidate).length;
  summary.textContent = `${filtered.length} results · ${candidateCount} location candidate${candidateCount === 1 ? "" : "s"}`;

  if (!filtered.length) {
    container.innerHTML = '<div class="empty-state"><p>No leads match current filters.</p></div>';
    return;
  }

  container.innerHTML = filtered.map((l, i) => {
    const conf = l.confidence || 0;
    const tier = conf >= 0.6 ? "high" : conf >= 0.3 ? "medium" : "low";
    const color = conf >= 0.6 ? "var(--green)" : conf >= 0.3 ? "var(--amber)" : "var(--text-muted)";
    const circumference = 2 * Math.PI * 22;
    const dashOffset = circumference - (conf * circumference);

    const approveActive = l.review_status === "credible" ? " is-active--approve" : "";
    const rejectActive = l.review_status === "not-relevant" ? " is-active--reject" : "";
    const analysis = l.analysis || {};
    const actionability = analysis.actionability_score || 0;
    const actionTier = analysis.actionability_label || "low";
    const extracted = (analysis.extracted_details || []).slice(0, 4);

    return `
      <div class="lead-card lead-card--${tier} animate-in" style="animation-delay:${Math.min(i * 30, 600)}ms" data-lead-id="${l.id}">
        <div class="confidence-meter" title="Case relevance score, not sighting likelihood">
          <svg class="confidence-meter__ring" viewBox="0 0 48 48">
            <circle class="confidence-meter__bg" cx="24" cy="24" r="22"/>
            <circle class="confidence-meter__fill" cx="24" cy="24" r="22"
              stroke="${color}"
              stroke-dasharray="${circumference}"
              stroke-dashoffset="${dashOffset}"/>
          </svg>
          <span class="confidence-meter__value" style="color:${color}">${(conf * 100).toFixed(0)}</span>
          <span class="confidence-meter__label">REL</span>
        </div>
        <div class="lead-card__body">
          <div class="lead-card__heading">
            <div class="lead-card__title">${escHtml(l.title || "Untitled Lead")}</div>
            <span class="actionability-pill actionability-pill--${actionTier}">${actionability} action</span>
          </div>
          <div class="lead-card__source">
            ${escHtml(l.source_name || "")} · 
            ${l.source_url ? `<a href="${escHtml(l.source_url)}" target="_blank" rel="noopener">View source ↗</a>` : "No URL"}
            ${l.published_at ? " · " + formatDate(l.published_at) : ""}
          </div>
          ${l.summary ? `<div class="lead-card__excerpt">${escHtml(l.summary)}</div>` : ""}
          ${l.content_excerpt ? `<div class="lead-card__excerpt">${escHtml(l.content_excerpt)}</div>` : ""}
          <div class="lead-card__tags">
            ${analysis.evidence_label ? `<span class="lead-tag lead-tag--evidence">${escHtml(analysis.evidence_label)}</span>` : ""}
            ${analysis.verification_label ? `<span class="lead-tag lead-tag--verification-${escHtml(analysis.verification_state)}">${escHtml(analysis.verification_label)}</span>` : ""}
            ${l.category ? `<span class="lead-tag lead-tag--category">${escHtml(l.category)}</span>` : ""}
            ${l.source_kind ? `<span class="lead-tag lead-tag--source">${escHtml(l.source_kind)}</span>` : ""}
            ${l.location_text ? `<span class="lead-tag">${escHtml(l.location_text)} · ${escHtml(analysis.location_precision || "area")}</span>` : ""}
            ${l.corroboration_count > 1 ? `<span class="lead-tag">×${l.corroboration_count} corroboration</span>` : ""}
          </div>
          ${extracted.length ? `
            <details class="lead-card__details">
              <summary>Extracted locating details</summary>
              <ul>${extracted.map((item) => `<li>${escHtml(item)}</li>`).join("")}</ul>
            </details>` : ""}
          ${analysis.next_step ? `<div class="lead-card__next"><strong>Next:</strong> ${escHtml(analysis.next_step)}</div>` : ""}
        </div>
        <div class="lead-card__actions">
          <button class="review-btn review-btn--approve${approveActive}" title="Mark credible" data-action="credible" data-lead-id="${l.id}">✓</button>
          <button class="review-btn review-btn--reject${rejectActive}" title="Not relevant" data-action="not-relevant" data-lead-id="${l.id}">✕</button>
          <button class="review-btn review-btn--flag" title="Flag for review" data-action="flag" data-lead-id="${l.id}">⚑</button>
        </div>
      </div>`;
  }).join("");

  // Review button handlers
  container.querySelectorAll(".review-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const leadId = Number(btn.dataset.leadId);
      const action = btn.dataset.action;
      await reviewLead(leadId, action);
    });
  });
}

function populateLeadSourceFilter() {
  const select = $("#leadFilterSource");
  const sources = [...new Set(state.leads.map((l) => l.source_name).filter(Boolean))].sort();
  const current = select.value;
  select.innerHTML = '<option value="">All Sources</option>' +
    sources.map((s) => `<option value="${escHtml(s)}">${escHtml(s)}</option>`).join("");
  select.value = current;
}

async function reviewLead(leadId, decision) {
  if (!state.apiOnline) {
    showToast("Backend required for lead review", "warning");
    return;
  }
  const data = await api(`/api/investigations/leads/${leadId}/review`, {
    method: "PATCH",
    body: JSON.stringify({ decision, notes: null }),
  });
  if (data) {
    // Update local state
    const lead = state.leads.find((l) => l.id === leadId);
    if (lead) {
      lead.reviewed = true;
      lead.review_status = decision;
      if (lead.analysis) {
        lead.analysis.verification_state = decision === "credible" ? "analyst_reviewed" : "unverified";
        lead.analysis.verification_label = decision === "credible" ? "Analyst reviewed" : "Unverified";
      }
    }
    renderLeads();
    generateAssessment();
    showToast(`Lead marked as ${decision}`, "success");
  }
}

// ─── Query Log ───
function renderQueryLog() {
  const container = $("#queryLog");
  if (!state.queryLogs.length) {
    container.innerHTML = '<div class="empty-state"><p>No query logs for this run.</p></div>';
    return;
  }

  container.innerHTML = `
    <table class="query-log-table">
      <colgroup>
        <col><col><col><col><col>
      </colgroup>
      <thead>
        <tr>
          <th>Connector</th>
          <th>Query</th>
          <th>Status</th>
          <th>Results</th>
          <th>Time</th>
        </tr>
      </thead>
      <tbody>
        ${state.queryLogs.map((q) => {
          const statusCls = q.status === "ok" || q.status === "completed" ? "status--ok"
            : q.status === "failed" ? "status--fail" : "status--warn";
          return `
            <tr>
              <td class="mono">${escHtml(q.connector_name || "")}</td>
              <td>${escHtml(q.query_used || "")}</td>
              <td class="${statusCls} mono">${escHtml(q.status || "—")}</td>
              <td class="mono">${q.result_count ?? "—"}</td>
              <td class="mono">${q.completed_at ? formatTime(q.completed_at) : "—"}</td>
            </tr>`;
        }).join("")}
      </tbody>
    </table>`;
}

// ─── Timeline ───
function renderTimeline() {
  const container = $("#timelineView");
  const c = state.selectedCase;
  
  const entries = [];

  // Official dates
  if (c?.missing_since) {
    entries.push({
      date: c.missing_since,
      title: `${c.name} reported missing`,
      desc: `${c.city || ""}${c.province ? ", " + c.province : ""}`,
      type: "official",
    });
  }

  // Lead dates
  state.leads.forEach((l) => {
    if (l.published_at) {
      entries.push({
        date: l.published_at,
        title: l.title || "Lead found",
        desc: `Source: ${l.source_name || "Unknown"} · Confidence: ${((l.confidence || 0) * 100).toFixed(0)}%`,
        type: l.confidence >= 0.6 ? "official" : l.category?.includes("news") ? "news" : "lead",
      });
    }
  });

  // Sort by date desc
  entries.sort((a, b) => new Date(b.date) - new Date(a.date));

  if (!entries.length) {
    container.innerHTML = '<div class="empty-state"><p>No timeline data available. Run an investigation to populate.</p></div>';
    return;
  }

  container.innerHTML = entries.slice(0, 50).map((e) => `
    <div class="timeline-entry">
      <div class="timeline-entry__dot timeline-entry__dot--${e.type}"></div>
      <div class="timeline-entry__content">
        <div class="timeline-entry__date">${formatDateTime(e.date)}</div>
        <div class="timeline-entry__title">${escHtml(e.title)}</div>
        ${e.desc ? `<div class="timeline-entry__desc">${escHtml(e.desc)}</div>` : ""}
      </div>
    </div>`).join("");
}

// ═══════════════════════════════════════════════════════════
// INTELLIGENCE ASSESSMENT (TraceLab-inspired)
// ═══════════════════════════════════════════════════════════

function generateAssessment() {
  const c = state.selectedCase;
  const leads = state.leads;
  if (!c || !leads.length) {
    $("#analysisEmpty").style.display = "";
    $("#analysisContent").style.display = "none";
    return;
  }

  $("#analysisEmpty").style.display = "none";
  $("#analysisContent").style.display = "";

  renderTriageSituation(c, leads);
  renderTriageMetrics(leads);
  renderLocationCandidates(leads);
  renderTriageActions(c, leads);
  renderTriageAuthorityBrief(c, leads);
}

function renderSituationOverview(c, leads) {
  const days = c.missing_since ? daysSince(c.missing_since) : null;
  const highLeads = leads.filter((l) => l.confidence >= 0.6).length;
  const sources = [...new Set(leads.map((l) => l.source_name).filter(Boolean))];
  const credible = leads.filter((l) => l.review_status === "credible").length;

  let trail = "cold";
  if (days !== null) {
    if (days <= 7) trail = "hot";
    else if (days <= 30) trail = "warm";
    else if (days <= 90) trail = "cooling";
  }

  const trailColors = { hot: "var(--red)", warm: "var(--amber)", cooling: "var(--amber-bright)", cold: "var(--text-muted)" };
  const trailLabels = { hot: "HOT (< 7 days)", warm: "WARM (< 30 days)", cooling: "COOLING (< 90 days)", cold: "COLD (> 90 days)" };

  const body = $("#analysisSituation");
  body.innerHTML = `
    <p><strong>${escHtml(c.name)}</strong> has been missing${days !== null ? ` for <strong>${days} days</strong>` : ""} from 
    <strong>${escHtml([c.city, c.province].filter(Boolean).join(", ") || "unknown location")}</strong>.
    ${c.status ? `Status: <strong>${escHtml(STATUS_LABELS[(c.status || "").toLowerCase()] || c.status)}</strong>.` : ""}</p>
    <p style="margin-top:8px">
      Trail classification: <span style="color:${trailColors[trail]};font-weight:700;font-family:var(--font-mono)">${trailLabels[trail]}</span>
    </p>
    <p style="margin-top:8px">
      MAAT gathered <strong>${leads.length} leads</strong> from <strong>${sources.length} sources</strong>.
      Of these, <strong>${highLeads}</strong> are high-confidence (≥ 60%) and <strong>${credible}</strong> have been manually verified as credible.
    </p>
    ${c.authority_name ? `<p style="margin-top:8px">Investigating authority: <strong>${escHtml(c.authority_name)}</strong>${c.authority_phone ? ` — ${escHtml(c.authority_phone)}` : ""}</p>` : ""}`;
}

function renderEvidenceMetricsSummary(leads) {
  const high = leads.filter((l) => l.confidence >= 0.6).length;
  const med = leads.filter((l) => l.confidence >= 0.3 && l.confidence < 0.6).length;
  const low = leads.filter((l) => l.confidence < 0.3).length;
  const credible = leads.filter((l) => l.review_status === "credible").length;
  const reviewed = leads.filter((l) => l.reviewed).length;

  const metricsEl = $("#evidenceMetrics");
  metricsEl.innerHTML = `
    <div class="evidence-metric">
      <div class="evidence-metric__value">${leads.length}</div>
      <div class="evidence-metric__label">Total Leads</div>
    </div>
    <div class="evidence-metric evidence-metric--green">
      <div class="evidence-metric__value">${high}</div>
      <div class="evidence-metric__label">High Confidence</div>
    </div>
    <div class="evidence-metric evidence-metric--teal">
      <div class="evidence-metric__value">${med}</div>
      <div class="evidence-metric__label">Medium Confidence</div>
    </div>
    <div class="evidence-metric evidence-metric--red">
      <div class="evidence-metric__value">${low}</div>
      <div class="evidence-metric__label">Low Confidence</div>
    </div>
    <div class="evidence-metric">
      <div class="evidence-metric__value">${credible}</div>
      <div class="evidence-metric__label">Verified Credible</div>
    </div>
    <div class="evidence-metric">
      <div class="evidence-metric__value">${reviewed}/${leads.length}</div>
      <div class="evidence-metric__label">Reviewed</div>
    </div>`;

  // Source breakdown bars
  const sourceCounts = {};
  leads.forEach((l) => {
    const cat = categorizeSource(l.source_name || "");
    sourceCounts[cat] = (sourceCounts[cat] || 0) + 1;
  });
  const sorted = Object.entries(sourceCounts).sort((a, b) => b[1] - a[1]);
  const max = sorted[0]?.[1] || 1;

  const breakdownEl = $("#evidenceBreakdown");
  breakdownEl.innerHTML = sorted.map(([cat, count]) => {
    const pct = Math.round((count / max) * 100);
    const cls = { news: "news", official: "official", social: "social", web: "web" }[cat] || "other";
    return `
      <div class="evidence-bar">
        <span class="evidence-bar__label">${escHtml(cat)}</span>
        <div class="evidence-bar__track">
          <div class="evidence-bar__fill evidence-bar__fill--${cls}" style="width:${pct}%"></div>
        </div>
        <span class="evidence-bar__count">${count}</span>
      </div>`;
  }).join("");
}

function categorizeSource(name) {
  const n = (name || "").toLowerCase();
  if (n.includes("news") || n.includes("gdelt") || n.includes("bing-news") || n.includes("google-news")) return "news";
  if (n.includes("official") || n.includes("canada-missing") || n.includes("mcsc") || n.includes("artifact")) return "official";
  if (n.includes("reddit") || n.includes("social") || n.includes("community")) return "social";
  if (n.includes("duckduckgo") || n.includes("wayback") || n.includes("searx") || n.includes("web")) return "web";
  return "other";
}

function renderLeadClusters(leads) {
  // Group by source category
  const clusters = {};
  leads.forEach((l) => {
    const cat = categorizeSource(l.source_name || "");
    if (!clusters[cat]) clusters[cat] = { leads: [], highCount: 0 };
    clusters[cat].leads.push(l);
    if (l.confidence >= 0.6) clusters[cat].highCount++;
  });

  const clusterEl = $("#clusterGrid");
  const sorted = Object.entries(clusters).sort((a, b) => b[1].highCount - a[1].highCount);
  
  clusterEl.innerHTML = sorted.map(([cat, data]) => {
    const bestLead = data.leads.sort((a, b) => (b.confidence || 0) - (a.confidence || 0))[0];
    const cls = { news: "news", official: "official", social: "social" }[cat] || "";
    return `
      <div class="cluster-card${cls ? " cluster-card--" + cls : ""}">
        <div class="cluster-card__name">${escHtml(capitalize(cat))} Intelligence</div>
        <div class="cluster-card__count">${data.leads.length} leads · ${data.highCount} high-confidence</div>
        <div class="cluster-card__desc">${bestLead ? escHtml(bestLead.title || "Strongest lead in this cluster") : "No leads"}</div>
      </div>`;
  }).join("");
}

function renderHypotheses(c, leads) {
  const hypotheses = buildHypotheses(c, leads);
  const listEl = $("#hypothesesList");

  if (!hypotheses.length) {
    listEl.innerHTML = '<p class="muted-text">Insufficient data to generate hypotheses. More leads are needed.</p>';
    return;
  }

  listEl.innerHTML = hypotheses.map((h, i) => {
    const confPct = Math.round(h.confidence * 100);
    const confColor = h.confidence >= 0.6 ? "var(--green)" : h.confidence >= 0.3 ? "var(--amber)" : "var(--text-muted)";
    return `
      <div class="hypothesis-card">
        <span class="hypothesis-card__rank">#${i + 1}</span>
        <div class="hypothesis-card__title">${escHtml(h.title)}</div>
        <div class="hypothesis-card__confidence">
          <div class="hypothesis-card__conf-bar">
            <div class="hypothesis-card__conf-fill" style="width:${confPct}%;background:${confColor}"></div>
          </div>
          <span class="hypothesis-card__conf-label" style="color:${confColor}">${confPct}% confidence</span>
        </div>
        <div class="hypothesis-card__body">${escHtml(h.description)}</div>
        <div class="hypothesis-card__evidence">
          ${h.evidence.map((e) => `<span class="hypothesis-evidence-tag">${escHtml(e)}</span>`).join("")}
        </div>
      </div>`;
  }).join("");
}

function buildHypotheses(c, leads) {
  const hypotheses = [];
  const days = c.missing_since ? daysSince(c.missing_since) : null;
  const highLeads = leads.filter((l) => l.confidence >= 0.6);
  const newsLeads = leads.filter((l) => categorizeSource(l.source_name) === "news");
  const socialLeads = leads.filter((l) => categorizeSource(l.source_name) === "social");
  const officialLeads = leads.filter((l) => categorizeSource(l.source_name) === "official");

  // Hypothesis: Active public awareness
  if (newsLeads.length >= 2) {
    const recentNews = newsLeads.filter((l) => l.published_at && daysSince(l.published_at) < 60);
    const conf = Math.min(0.8, 0.3 + (recentNews.length * 0.1) + (highLeads.length * 0.05));
    hypotheses.push({
      title: "Active Media Coverage Suggests Ongoing Public Awareness",
      description: `${newsLeads.length} news sources are covering this case${recentNews.length ? `, with ${recentNews.length} articles in the last 60 days` : ""}. This indicates continued public visibility which increases the probability of community tips and sightings.`,
      confidence: Math.min(conf, 0.85),
      evidence: newsLeads.slice(0, 3).map((l) => l.source_name || "News"),
    });
  }

  // Hypothesis: Community engagement
  if (socialLeads.length >= 1) {
    const conf = Math.min(0.7, 0.2 + (socialLeads.length * 0.1));
    hypotheses.push({
      title: "Community Engagement Detected on Social Platforms",
      description: `${socialLeads.length} social media source${socialLeads.length > 1 ? "s" : ""} show public discussion about this case. Community-sourced leads can surface sighting information not captured by official channels. These should be triaged carefully for actionable details.`,
      confidence: Math.min(conf, 0.75),
      evidence: socialLeads.slice(0, 3).map((l) => l.source_name || "Social"),
    });
  }

  // Hypothesis: Trail temperature
  if (days !== null && days > 30) {
    const coldness = Math.min(1, days / 365);
    const recentLeads = leads.filter((l) => l.published_at && daysSince(l.published_at) < 30);
    if (recentLeads.length === 0) {
      hypotheses.push({
        title: "Cold Trail — Limited Recent Activity",
        description: `${days} days have passed since the disappearance with no leads in the last 30 days. The trail is cooling significantly. Renewed media pushes, anniversary coverage, or targeted social media campaigns may help resurface the case.`,
        confidence: 0.5 + (coldness * 0.2),
        evidence: ["Timeline gap", `${days}d elapsed`, "No recent leads"],
      });
    } else {
      hypotheses.push({
        title: "Sustained Lead Generation Despite Elapsed Time",
        description: `Despite ${days} days elapsed, ${recentLeads.length} lead${recentLeads.length > 1 ? "s" : ""} appeared in the last 30 days. The case maintains visibility. This sustained activity is a positive signal for resolution.`,
        confidence: 0.4 + (recentLeads.length * 0.08),
        evidence: [`${recentLeads.length} recent leads`, `${days}d elapsed`],
      });
    }
  }

  // Hypothesis: Official records completeness
  if (officialLeads.length >= 1) {
    hypotheses.push({
      title: "Official Sources Provide Foundation for Investigation",
      description: `${officialLeads.length} official source${officialLeads.length > 1 ? "s" : ""} confirm and enrich the case data. Cross-referencing official records with OSINT findings helps validate lead accuracy and fills information gaps.`,
      confidence: 0.6,
      evidence: officialLeads.slice(0, 3).map((l) => l.source_name || "Official"),
    });
  }

  // Hypothesis: Geospatial pattern detection
  if (c.latitude && c.longitude) {
    const geoLeads = leads.filter((l) => l.latitude && l.longitude);
    if (geoLeads.length >= 2) {
      hypotheses.push({
        title: "Geographic Clustering Detected in Lead Locations",
        description: `${geoLeads.length} leads have geographic coordinates, allowing spatial pattern analysis. Clusters near transit corridors, border crossings, or highway routes may indicate travel vectors. Review the Evidence Map tab for visualization.`,
        confidence: 0.35 + (geoLeads.length * 0.05),
        evidence: [`${geoLeads.length} geo-located leads`, `Origin: ${escHtml(c.city || c.province || "Unknown")}`],
      });
    }
  }

  // Sort by confidence
  hypotheses.sort((a, b) => b.confidence - a.confidence);
  return hypotheses;
}

function renderActionItems(c, leads) {
  const items = [];
  const days = c.missing_since ? daysSince(c.missing_since) : null;
  const highLeads = leads.filter((l) => l.confidence >= 0.6);
  const unreviewed = leads.filter((l) => !l.reviewed);

  // Always recommend reviewing high-confidence leads
  if (highLeads.length > 0 && unreviewed.length > 0) {
    items.push({
      priority: "high",
      title: `Review ${Math.min(highLeads.length, unreviewed.length)} unreviewed high-confidence leads`,
      desc: "Triage leads in the Leads tab. Mark credible findings and dismiss duplicates to refine the assessment.",
    });
  }

  // Report to authority
  if (c.authority_name) {
    items.push({
      priority: "high",
      title: `Share OSINT findings with ${c.authority_name}`,
      desc: `Contact the investigating authority${c.authority_phone ? ` at ${c.authority_phone}` : ""} with credible leads. Use the Authority Brief above.`,
    });
  }

  // Cold trail actions
  if (days && days > 90) {
    items.push({
      priority: "medium",
      title: "Consider social media renewal campaign",
      desc: `The case is ${days} days old. A targeted social media push (anniversary posts, community shares) can resurface public attention.`,
    });
  }

  // Reverse image suggestion
  if (c.photo_url) {
    items.push({
      priority: "medium",
      title: "Run reverse image search on case photo",
      desc: "Upload the case photo to public reverse image tools (Google Images, TinEye) to detect reuse on social profiles or media not captured by text-based connectors.",
    });
  }

  // Review web archives
  items.push({
    priority: "low",
    title: "Check Wayback Machine for deleted content",
    desc: "Cached pages and deleted social profiles may contain information no longer publicly accessible. Review Wayback connector results for archived snapshots.",
  });

  const container = $("#actionItems");
  container.innerHTML = items.map((item) => `
    <div class="action-item">
      <span class="action-item__priority action-item__priority--${item.priority}">${item.priority}</span>
      <div class="action-item__body">
        <div class="action-item__title">${escHtml(item.title)}</div>
        <div class="action-item__desc">${escHtml(item.desc)}</div>
      </div>
    </div>`).join("");
}

function renderAuthorityBrief(c, leads) {
  const days = c.missing_since ? daysSince(c.missing_since) : null;
  const high = leads.filter((l) => l.confidence >= 0.6);
  const credible = leads.filter((l) => l.review_status === "credible");
  const sources = [...new Set(leads.map((l) => l.source_name).filter(Boolean))];

  let brief = `MAAT INTELLIGENCE BRIEF\n`;
  brief += `Generated: ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC\n`;
  brief += `${"─".repeat(50)}\n\n`;
  brief += `SUBJECT: ${c.name || "Unknown"}\n`;
  brief += `STATUS: ${STATUS_LABELS[(c.status || "").toLowerCase()] || c.status || "Unknown"}\n`;
  brief += `LOCATION: ${[c.city, c.province].filter(Boolean).join(", ") || "Unknown"}\n`;
  if (c.missing_since) brief += `MISSING SINCE: ${formatDate(c.missing_since)} (${days} days)\n`;
  if (c.authority_name) brief += `AUTHORITY: ${c.authority_name}\n`;
  brief += `\nINTELLIGENCE SUMMARY\n`;
  brief += `${"─".repeat(30)}\n`;
  brief += `Total leads gathered: ${leads.length}\n`;
  brief += `High-confidence leads: ${high.length}\n`;
  brief += `Manually verified credible: ${credible.length}\n`;
  brief += `Sources queried: ${sources.length} (${sources.join(", ")})\n`;

  if (high.length) {
    brief += `\nTOP LEADS\n`;
    brief += `${"─".repeat(30)}\n`;
    high.slice(0, 5).forEach((l, i) => {
      brief += `${i + 1}. [${((l.confidence || 0) * 100).toFixed(0)}%] ${l.title || "Untitled"}\n`;
      brief += `   Source: ${l.source_name || "Unknown"}`;
      if (l.source_url) brief += ` | ${l.source_url}`;
      brief += `\n`;
    });
  }

  if (credible.length) {
    brief += `\nVERIFIED CREDIBLE LEADS\n`;
    brief += `${"─".repeat(30)}\n`;
    credible.forEach((l, i) => {
      brief += `${i + 1}. ${l.title || "Untitled"} (${l.source_name || "Unknown"})\n`;
    });
  }

  brief += `\nDISCLAIMER: This brief is machine-generated from public OSINT sources.\nAll findings are unverified and should be shared with the investigating authority.\nDo not act independently — report, don't intervene.\n`;

  const briefEl = $("#briefOutput");
  briefEl.textContent = brief;
}

// ═══════════════════════════════════════════════════════════
// MAPS
// ═══════════════════════════════════════════════════════════

function renderTriageSituation(c, leads) {
  const days = c.missing_since ? daysSince(c.missing_since) : null;
  const sources = [...new Set(leads.map((l) => l.source_name).filter(Boolean))];
  const candidates = leads.filter((l) => l.analysis?.is_location_candidate);
  const reviewedCandidates = candidates.filter((l) => l.review_status === "credible");
  const preciseCandidates = candidates.filter((l) =>
    ["address", "street", "neighbourhood", "street_or_landmark"].includes(l.analysis?.location_precision)
  );

  $("#analysisSituation").innerHTML = `
    <p><strong>${escHtml(c.name)}</strong> has been missing${days !== null ? ` for <strong>${days} days</strong>` : ""} from
    <strong>${escHtml([c.city, c.province].filter(Boolean).join(", ") || "an unknown location")}</strong>.
    ${c.status ? `Status: <strong>${escHtml(STATUS_LABELS[(c.status || "").toLowerCase()] || c.status)}</strong>.` : ""}</p>
    <p style="margin-top:8px">
      This run returned <strong>${leads.length} public-source results</strong> from <strong>${sources.length} sources</strong>.
      <strong>${candidates.length}</strong> contain a reported location; <strong>${preciseCandidates.length}</strong> are more precise than city level.
    </p>
    <p style="margin-top:8px">
      <strong>${reviewedCandidates.length}</strong> location candidate${reviewedCandidates.length === 1 ? " has" : "s have"} been marked credible by an analyst.
      All other places remain unverified and must not be treated as the person's current location.
    </p>
    ${c.authority_name ? `<p style="margin-top:8px">Investigating authority: <strong>${escHtml(c.authority_name)}</strong>${c.authority_phone ? ` | ${escHtml(c.authority_phone)}` : ""}</p>` : ""}`;
}

function renderTriageMetrics(leads) {
  const candidates = leads.filter((l) => l.analysis?.is_location_candidate).length;
  const actionable = leads.filter((l) => (l.analysis?.actionability_score || 0) >= 65).length;
  const geocoded = leads.filter((l) => l.analysis?.is_location_candidate && l.analysis?.has_coordinates).length;
  const reviewed = leads.filter((l) => l.review_status === "credible").length;
  const contextOnly = leads.filter((l) => l.analysis?.evidence_type === "context_only").length;

  $("#evidenceMetrics").innerHTML = `
    <div class="evidence-metric">
      <div class="evidence-metric__value">${leads.length}</div>
      <div class="evidence-metric__label">Search Results</div>
    </div>
    <div class="evidence-metric evidence-metric--green">
      <div class="evidence-metric__value">${candidates}</div>
      <div class="evidence-metric__label">Location Candidates</div>
    </div>
    <div class="evidence-metric evidence-metric--teal">
      <div class="evidence-metric__value">${actionable}</div>
      <div class="evidence-metric__label">High Actionability</div>
    </div>
    <div class="evidence-metric">
      <div class="evidence-metric__value">${geocoded}</div>
      <div class="evidence-metric__label">Mapped Places</div>
    </div>
    <div class="evidence-metric">
      <div class="evidence-metric__value">${reviewed}</div>
      <div class="evidence-metric__label">Analyst Reviewed</div>
    </div>
    <div class="evidence-metric evidence-metric--red">
      <div class="evidence-metric__value">${contextOnly}</div>
      <div class="evidence-metric__label">Context Only</div>
    </div>`;

  const typeCounts = {};
  leads.forEach((lead) => {
    const label = lead.analysis?.evidence_label || "Unclassified";
    typeCounts[label] = (typeCounts[label] || 0) + 1;
  });
  const sorted = Object.entries(typeCounts).sort((a, b) => b[1] - a[1]);
  const max = sorted[0]?.[1] || 1;
  $("#evidenceBreakdown").innerHTML = sorted.map(([label, count], index) => `
    <div class="evidence-bar">
      <span class="evidence-bar__label">${escHtml(label)}</span>
      <div class="evidence-bar__track">
        <div class="evidence-bar__fill evidence-bar__fill--${["official", "news", "web", "other"][Math.min(index, 3)]}" style="width:${Math.round((count / max) * 100)}%"></div>
      </div>
      <span class="evidence-bar__count">${count}</span>
    </div>`).join("");
}

function renderLocationCandidates(leads) {
  const candidates = leads
    .filter((l) => l.analysis?.is_location_candidate)
    .sort((a, b) => (b.analysis?.actionability_score || 0) - (a.analysis?.actionability_score || 0));
  const container = $("#locationCandidates");

  if (!candidates.length) {
    container.innerHTML = `
      <div class="location-candidate-empty">
        <strong>No new reported location survived extraction.</strong>
        <span>The results currently provide awareness or case context, not a source-backed place to verify.</span>
      </div>`;
    return;
  }

  container.innerHTML = candidates.map((lead, index) => {
    const analysis = lead.analysis || {};
    const details = (analysis.extracted_details || []).slice(0, 5);
    const tier = analysis.actionability_label || "low";
    return `
      <article class="location-candidate location-candidate--${tier}">
        <div class="location-candidate__rank">${String(index + 1).padStart(2, "0")}</div>
        <div class="location-candidate__body">
          <div class="location-candidate__heading">
            <div>
              <div class="location-candidate__place">${escHtml(lead.location_text || "Reported place")}</div>
              <div class="location-candidate__meta">
                ${escHtml(analysis.verification_label || "Unverified")} | ${escHtml(analysis.location_precision || "unknown precision")} | ${escHtml(lead.source_name || "Unknown source")}
              </div>
            </div>
            <div class="location-candidate__score">${analysis.actionability_score || 0}<span>action</span></div>
          </div>
          <div class="location-candidate__title">${escHtml(lead.title || "Untitled source result")}</div>
          ${details.length ? `<ul class="location-candidate__details">${details.map((item) => `<li>${escHtml(item)}</li>`).join("")}</ul>` : ""}
          <div class="location-candidate__next"><strong>Verify:</strong> ${escHtml(analysis.next_step || "Open the source and validate the reported place.")}</div>
          ${lead.source_url ? `<a class="location-candidate__source" href="${escHtml(lead.source_url)}" target="_blank" rel="noopener noreferrer">Open source</a>` : ""}
        </div>
      </article>`;
  }).join("");
}

function renderTriageActions(c, leads) {
  const candidates = leads
    .filter((l) => l.analysis?.is_location_candidate)
    .sort((a, b) => (b.analysis?.actionability_score || 0) - (a.analysis?.actionability_score || 0));
  const unreviewed = leads.filter((l) => !l.reviewed);
  const items = [];

  candidates.slice(0, 4).forEach((lead) => {
    items.push({
      priority: (lead.analysis?.actionability_score || 0) >= 65 ? "high" : "medium",
      title: `Verify reported location: ${lead.location_text || "unspecified place"}`,
      desc: `${lead.source_name || "Public source"}: ${lead.analysis?.next_step || "Open and validate the source details."}`,
    });
  });

  if (!candidates.length) {
    items.push({
      priority: "medium",
      title: "No source-backed location candidate yet",
      desc: "Review the highest-actionability results for a date, place, vehicle, witness detail, or direction of travel before escalating anything.",
    });
  }
  if (unreviewed.length) {
    items.push({
      priority: "medium",
      title: `Triage ${unreviewed.length} unreviewed result${unreviewed.length === 1 ? "" : "s"}`,
      desc: "Dismiss amplification and namesakes first, then preserve URLs and excerpts for results that add locating detail.",
    });
  }
  if (c.authority_name) {
    items.push({
      priority: "high",
      title: `Send credible details to ${c.authority_name}`,
      desc: `Use the brief below${c.authority_phone ? ` and call ${c.authority_phone}` : ""}. Do not contact the subject, relatives, or witnesses directly.`,
    });
  }

  $("#actionItems").innerHTML = items.map((item) => `
    <div class="action-item">
      <span class="action-item__priority action-item__priority--${item.priority}">${item.priority}</span>
      <div class="action-item__body">
        <div class="action-item__title">${escHtml(item.title)}</div>
        <div class="action-item__desc">${escHtml(item.desc)}</div>
      </div>
    </div>`).join("");
}

function renderTriageAuthorityBrief(c, leads) {
  const days = c.missing_since ? daysSince(c.missing_since) : null;
  const candidates = leads
    .filter((l) => l.analysis?.is_location_candidate)
    .sort((a, b) => (b.analysis?.actionability_score || 0) - (a.analysis?.actionability_score || 0));
  const reviewed = candidates.filter((l) => l.review_status === "credible");

  let brief = "MAAT PUBLIC-SOURCE LOCATION BRIEF\n";
  brief += `Generated: ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC\n`;
  brief += "--------------------------------------------------\n\n";
  brief += `SUBJECT: ${c.name || "Unknown"}\n`;
  brief += `LAST KNOWN AREA: ${[c.city, c.province].filter(Boolean).join(", ") || "Unknown"}\n`;
  if (c.missing_since) brief += `MISSING SINCE: ${formatDate(c.missing_since)}${days !== null ? ` (${days} days)` : ""}\n`;
  if (c.authority_name) brief += `AUTHORITY: ${c.authority_name}${c.authority_phone ? ` | ${c.authority_phone}` : ""}\n`;
  brief += `\nRESULTS REVIEWED BY SYSTEM: ${leads.length}\n`;
  brief += `REPORTED LOCATION CANDIDATES: ${candidates.length}\n`;
  brief += `MARKED CREDIBLE BY ANALYST: ${reviewed.length}\n`;

  if (candidates.length) {
    brief += "\nLOCATION CANDIDATES - VERIFY BEFORE USE\n";
    brief += "--------------------------------------------------\n";
    candidates.slice(0, 8).forEach((lead, index) => {
      brief += `${index + 1}. ${lead.location_text || "Unspecified place"} [${lead.analysis?.verification_label || "Unverified"}]\n`;
      brief += `   Actionability: ${lead.analysis?.actionability_score || 0}/100; precision: ${lead.analysis?.location_precision || "unknown"}\n`;
      brief += `   Source: ${lead.source_name || "Unknown"} | ${lead.title || "Untitled"}\n`;
      if (lead.source_url) brief += `   URL: ${lead.source_url}\n`;
      (lead.analysis?.extracted_details || []).slice(0, 3).forEach((detail) => {
        brief += `   - ${detail}\n`;
      });
    });
  }

  brief += "\nCAUTION: Machine-extracted locations are unverified public-source reports.\n";
  brief += "They do not establish the person's current location. Preserve sources and report credible details to authorities.\n";
  $("#briefOutput").textContent = brief;
}

function initOverviewMap() {
  const container = $("#overviewMap");
  if (!container || state.maps.overview) return;
  
  const map = L.map(container, {
    zoomControl: false,
    attributionControl: false,
  }).setView(CONFIG.MAP_CENTER, CONFIG.MAP_ZOOM);
  
  L.tileLayer(CONFIG.TILE_URL, { attribution: CONFIG.TILE_ATTR }).addTo(map);
  L.control.zoom({ position: "topright" }).addTo(map);
  state.maps.overview = map;

  // Add case markers
  setTimeout(() => updateOverviewMapMarkers(), 100);
}

function updateOverviewMapMarkers() {
  const map = state.maps.overview;
  if (!map) return;

  // Clear existing
  if (state.maps.overviewMarkers) {
    state.maps.overviewMarkers.forEach((m) => map.removeLayer(m));
  }
  state.maps.overviewMarkers = [];

  const bounds = [];
  state.cases.forEach((c) => {
    if (c.latitude && c.longitude) {
      const marker = L.circleMarker([c.latitude, c.longitude], {
        radius: 5,
        fillColor: "#f5a623",
        fillOpacity: 0.6,
        color: "#f5a623",
        weight: 1,
        opacity: 0.8,
      }).addTo(map);
      
      marker.bindPopup(`
        <div style="font-family:var(--font-body);min-width:160px">
          <strong>${escHtml(c.name)}</strong><br>
          <span style="font-size:12px;opacity:0.8">${escHtml(c.city || "")}${c.province ? ", " + escHtml(c.province) : ""}</span>
        </div>
      `);
      
      marker.on("click", () => selectCase(c.id));
      state.maps.overviewMarkers.push(marker);
      bounds.push([c.latitude, c.longitude]);
    }
  });

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 6 });
  }
}

function renderIntelMap() {
  const container = $("#intelMap");
  const c = state.selectedCase;

  // Destroy previous
  if (state.maps.intel) {
    state.maps.intel.remove();
    state.maps.intel = null;
  }

  if (!c) return;

  const lat = c.latitude || 56.1;
  const lng = c.longitude || -96.0;
  const zoom = c.latitude ? 10 : 4;

  const map = L.map(container, {
    zoomControl: false,
    attributionControl: false,
  }).setView([lat, lng], zoom);

  L.tileLayer(CONFIG.TILE_URL, { attribution: CONFIG.TILE_ATTR }).addTo(map);
  state.maps.intel = map;

  if (c.latitude && c.longitude) {
    L.circleMarker([c.latitude, c.longitude], {
      radius: 8,
      fillColor: "#f87171",
      fillOpacity: 0.7,
      color: "#f87171",
      weight: 2,
    }).addTo(map).bindPopup(`<strong>${escHtml(c.name)}</strong><br>Last seen location`);
  }
}

function renderEvidenceMap() {
  const container = $("#evidenceMap");
  const c = state.selectedCase;

  // Destroy previous
  if (state.maps.evidence) {
    state.maps.evidence.remove();
    state.maps.evidence = null;
  }

  const lat = c?.latitude || 56.1;
  const lng = c?.longitude || -96.0;
  const zoom = c?.latitude ? 8 : 4;

  const map = L.map(container, {
    attributionControl: false,
  }).setView([lat, lng], zoom);

  L.tileLayer(CONFIG.TILE_URL, { attribution: CONFIG.TILE_ATTR }).addTo(map);
  state.maps.evidence = map;

  // Case marker
  if (c?.latitude && c?.longitude) {
    L.circleMarker([c.latitude, c.longitude], {
      radius: 10,
      fillColor: "#f87171",
      fillOpacity: 0.8,
      color: "#fff",
      weight: 2,
    }).addTo(map).bindPopup(`<strong>${escHtml(c.name)}</strong><br>Last seen location`).openPopup();
  }

  // Lead markers
  const bounds = [];
  if (c?.latitude && c?.longitude) bounds.push([c.latitude, c.longitude]);

  state.leads.forEach((l) => {
    const analysis = l.analysis || {};
    if (l.latitude && l.longitude && analysis.evidence_type !== "research_tool" && analysis.evidence_type !== "context_only") {
      const actionability = analysis.actionability_score || 0;
      const color = analysis.verification_state === "official_source" ? "#4ade80"
        : analysis.verification_state === "analyst_reviewed" ? "#38bdf8" : "#f5a623";
      const uncertaintyRadius = {
        address: 250,
        street: 750,
        neighbourhood: 3000,
        street_or_landmark: 1500,
        city: 12000,
        city_or_area: 15000,
        area: 20000,
      }[analysis.location_precision] || 12000;

      L.circle([l.latitude, l.longitude], {
        radius: uncertaintyRadius,
        fillColor: color,
        fillOpacity: 0.06,
        color,
        weight: 1,
        opacity: 0.35,
      }).addTo(map);
      
      L.circleMarker([l.latitude, l.longitude], {
        radius: analysis.is_location_candidate ? 7 : 5,
        fillColor: color,
        fillOpacity: 0.8,
        color: color,
        weight: 2,
      }).addTo(map).bindPopup(`
        <div style="min-width:180px;font-family:var(--font-body)">
          <strong>${escHtml(l.location_text || l.title || "Mapped evidence")}</strong><br>
          <span style="font-size:11px;opacity:0.8">${escHtml(analysis.evidence_label || "Location mention")} | ${escHtml(analysis.verification_label || "Unverified")}</span><br>
          <span style="font-size:11px;opacity:0.8">${escHtml(analysis.location_precision || "area")} precision | ${actionability}/100 actionability</span><br>
          <span style="font-size:11px;opacity:0.8">${escHtml(l.source_name || "Unknown source")}</span>
        </div>
      `);
      bounds.push([l.latitude, l.longitude]);
    }
  });

  if (bounds.length > 1) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }
}

// ═══════════════════════════════════════════════════════════
// INVESTIGATION ACTIONS
// ═══════════════════════════════════════════════════════════

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pollInvestigationRun(runId, caseId, timeoutMs = 8 * 60 * 1000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await delay(2500);
    const run = await api(`/api/investigations/runs/${runId}`);
    if (!run) continue;
    if (["completed", "completed_with_warnings", "failed"].includes(run.status)) {
      return run;
    }
    if (state.selectedCaseId === caseId) {
      const btn = $("#runInvestigationBtn");
      btn.lastChild.textContent = run.status === "queued" ? " Queued…" : " Investigating…";
    }
  }
  return null;
}

async function runInvestigation() {
  if (!state.apiOnline) {
    showToast("Backend must be running to investigate", "error");
    return;
  }
  if (!state.selectedCaseId) return;

  const btn = $("#runInvestigationBtn");
  btn.classList.add("is-running");
  btn.innerHTML = `
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M15.312 11.424a5.5 5.5 0 01-9.379 2.624l-1.06 1.06a7 7 0 0011.558-3.534l.03-.15-1.149-.15v.15zm-10.624-2.85a5.5 5.5 0 019.38-2.623l1.06-1.06A7 7 0 003.57 8.424l-.03.15 1.15.15v-.15z"/></svg>
    Investigating…`;
  
  showToast("Investigation started. OSINT connectors are running…", "info");

  let data = null;
  try {
    const res = await fetch(`${state.apiBase}/api/investigations/${state.selectedCaseId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (res.status === 403) {
      const err = await res.json().catch(() => ({}));
      const detail = err.detail || "Investigator mode is disabled.";
      btn.classList.remove("is-running");
      btn.innerHTML = `
        <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/></svg>
        Run Investigation`;
      showToast(`Backend: ${detail} Set ENABLE_INVESTIGATOR_MODE=true in your Render environment.`, "error");
      return;
    }
    if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
    data = await res.json();
  } catch (err) {
    console.error("Investigation error:", err);
  }
  
  if (data) {
    showToast(`Run #${data.run_id} queued — ${data.connectors?.length || 0} public-source methods scheduled.`, "info");
    const completed = await pollInvestigationRun(data.run_id, state.selectedCaseId);
    await loadCaseRuns(state.selectedCaseId);
    if (!completed) {
      showToast(`Run #${data.run_id} is still processing. It will remain in Run History.`, "warning");
    } else if (completed.status === "failed") {
      showToast(`Run #${data.run_id} failed: ${completed.error_message || "review the Query Log"}`, "error");
    } else {
      const leadCount = completed.stats?.total_leads || 0;
      const warning = completed.status === "completed_with_warnings" ? " with connector warnings" : "";
      showToast(`Run #${data.run_id} complete${warning}: ${leadCount} source-linked results.`, "success");
    }
  } else {
    showToast("Investigation failed. Check backend logs for details.", "error");
  }

  btn.classList.remove("is-running");
  btn.innerHTML = `
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/></svg>
    Run Investigation`;
}

async function syncCases() {
  if (!state.apiOnline) {
    showToast("Backend required for sync", "warning");
    return;
  }

  const btn = $("#syncBtn");
  btn.classList.add("is-loading");
  showToast("Syncing cases from MCSC ArcGIS…", "info");

  const data = await api("/api/sync/cases", { method: "POST" });
  
  btn.classList.remove("is-loading");

  if (data) {
    showToast(`Sync complete! ${data.synced || data.total || "?"} cases updated.`, "success");
    // Reload cases
    await initCaseData();
  } else {
    showToast("Sync failed", "error");
  }
}

// ═══════════════════════════════════════════════════════════
// VIEW NAVIGATION
// ═══════════════════════════════════════════════════════════

function showView(name) {
  state.view = name;
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view${capitalize(name)}`));
}

function switchTab(tabName) {
  $$(".tab-btn").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === tabName));
  $$(".tab-content").forEach((t) => t.classList.toggle("is-active", t.dataset.tabContent === tabName));

  // Lazy init maps when switching to map tab
  if (tabName === "map") {
    setTimeout(() => renderEvidenceMap(), 100);
  }
  // Generate assessment when switching to analysis tab
  if (tabName === "analysis") {
    generateAssessment();
  }
}

// ═══════════════════════════════════════════════════════════
// FILTERING & SEARCH
// ═══════════════════════════════════════════════════════════

function filterCases() {
  const search = ($("#globalSearch").value || "").toLowerCase().trim();
  const province = $("#filterProvince").value;
  const status = $("#filterStatus").value;
  const sort = $("#filterSort").value;

  let filtered = [...state.cases];

  if (search) {
    filtered = filtered.filter((c) => {
      const text = [c.name, c.city, c.province, c.status].filter(Boolean).join(" ").toLowerCase();
      return text.includes(search);
    });
  }
  if (province) {
    filtered = filtered.filter((c) => c.province === province);
  }
  if (status) {
    filtered = filtered.filter((c) => c.status === status);
  }

  // Sort
  filtered.sort((a, b) => {
    switch (sort) {
      case "name": return (a.name || "").localeCompare(b.name || "");
      case "province": return (a.province || "").localeCompare(b.province || "");
      case "age-asc": return (a.age || 99) - (b.age || 99);
      case "recent":
      default: return new Date(b.missing_since || 0) - new Date(a.missing_since || 0);
    }
  });

  state.filteredCases = filtered;
  $("#caseCount").textContent = String(filtered.length);
  renderCaseList();
}

function populateProvinceFilter() {
  const select = $("#filterProvince");
  const provinces = [...new Set(state.cases.map((c) => c.province).filter(Boolean))].sort();
  select.innerHTML = '<option value="">All Provinces</option>' +
    provinces.map((p) => `<option value="${escHtml(p)}">${escHtml(p)}</option>`).join("");
}

// ═══════════════════════════════════════════════════════════
// TOASTS & MODALS
// ═══════════════════════════════════════════════════════════

function showToast(message, type = "info") {
  const container = $("#toastContainer");
  const icons = { success: "✓", error: "✕", warning: "⚠", info: "ℹ" };
  
  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  toast.innerHTML = `
    <span class="toast__icon">${icons[type] || "ℹ"}</span>
    <span class="toast__text">${escHtml(message)}</span>
    <button class="toast__close" onclick="this.parentElement.remove()">×</button>`;
  
  container.appendChild(toast);
  
  setTimeout(() => {
    toast.classList.add("is-exiting");
    setTimeout(() => toast.remove(), 300);
  }, 5000);
}

function showModal(html) {
  $("#modalBody").innerHTML = html;
  $("#modalOverlay").classList.add("is-open");
}

function closeModal() {
  $("#modalOverlay").classList.remove("is-open");
}

// ═══════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════

function escHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = String(str);
  return div.innerHTML;
}

function sanitizeHtml(value) {
  const ALLOWED_TAGS = new Set(["p", "br", "strong", "em", "b", "i", "ul", "ol", "li", "a", "span", "div"]);
  const ALLOWED_ATTRS = { a: new Set(["href", "target", "rel"]) };
  const tmp = document.createElement("div");
  tmp.innerHTML = String(value || "");
  tmp.querySelectorAll("script,style,iframe,object,embed,form,input,textarea,link,meta,base,svg,math").forEach(
    (el) => el.remove()
  );
  tmp.querySelectorAll("*").forEach((el) => {
    const tag = el.tagName.toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) {
      el.replaceWith(...el.childNodes);
      return;
    }
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on") || name === "style" || name === "class" || name === "id") {
        el.removeAttribute(attr.name);
      } else if (!(ALLOWED_ATTRS[tag] || new Set()).has(name)) {
        el.removeAttribute(attr.name);
      }
    }
    if (tag === "a") {
      const href = el.getAttribute("href") || "";
      if (!/^https?:\/\//i.test(href) && !href.startsWith("mailto:")) {
        el.removeAttribute("href");
      }
      el.setAttribute("target", "_blank");
      el.setAttribute("rel", "noopener noreferrer");
    }
  });
  return tmp.innerHTML;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" });
  } catch { return String(iso).slice(0, 10); }
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-CA", {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return String(iso).slice(0, 16); }
}

function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return "—"; }
}

function daysSince(iso) {
  if (!iso) return 0;
  const diff = Date.now() - new Date(iso).getTime();
  return Math.max(0, Math.floor(diff / 86400000));
}

function elapsedText(iso) {
  const d = daysSince(iso);
  if (d === 0) return "Today";
  if (d === 1) return "1 day ago";
  if (d < 30) return `${d} days ago`;
  if (d < 365) {
    const m = Math.floor(d / 30);
    return `${m} month${m > 1 ? "s" : ""} ago`;
  }
  const y = Math.floor(d / 365);
  return `${y} year${y > 1 ? "s" : ""} ago`;
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function truncateUrl(url, maxLen = 50) {
  if (!url || url.length <= maxLen) return url;
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    const path = u.pathname;
    const available = maxLen - host.length - 3; // 3 for "…"
    if (available <= 0) return host + "…";
    if (path.length <= available) return host + path;
    return host + path.slice(0, available) + "…";
  } catch { return url.slice(0, maxLen) + "…"; }
}

function updateQuickActions() {
  const c = state.selectedCase;
  const link = $("#qaReportAuthority");
  if (c?.authority_phone) {
    link.href = `tel:${c.authority_phone}`;
    link.innerHTML = `<span class="qa-link__icon">📞</span> Call ${escHtml(c.authority_name || "Authority")}`;
  } else {
    link.href = "#";
    link.innerHTML = '<span class="qa-link__icon">📞</span> Report to Authority';
  }
}

function updateClock() {
  const el = $("#systemClock");
  if (el) {
    el.textContent = new Date().toLocaleTimeString("en-CA", {
      hour: "2-digit", minute: "2-digit",
    });
  }
}

// ═══════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════

async function initCaseData() {
  state.cases = await loadCases();
  state.filteredCases = [...state.cases];

  populateProvinceFilter();
  filterCases();
  renderOverviewStats();
  renderProvinceChart();
  renderPriorityCases();
  renderConnectorGrid();
  renderConnectorDots();
  updateOverviewMapMarkers();
}

async function boot() {
  // Clock
  updateClock();
  setInterval(updateClock, 30000);

  // Detect backend
  const statusEl = $("#apiStatus");
  const backendLabel = $("#backendUrl");
  await detectApi();
  
  if (state.apiOnline) {
    statusEl.classList.add("is-online");
    statusEl.querySelector(".status-beacon__label").textContent = "API Online";
    backendLabel.textContent = `Backend: ${state.apiBase}`;
    $("#lastSyncTime").textContent = "Backend connected";
  } else {
    statusEl.classList.add("is-offline");
    statusEl.querySelector(".status-beacon__label").textContent = "API Offline";
    backendLabel.textContent = "Backend: offline — using fallback data";
    showToast("Backend offline. Using ArcGIS direct feed or bundled data.", "warning");
  }

  // Load cases
  await initCaseData();

  // Init overview map
  setTimeout(() => initOverviewMap(), 200);

  // ─── Event Listeners ───

  // Search
  let searchTimer;
  $("#globalSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(filterCases, 200);
  });

  $("#mobileCasesBtn").addEventListener("click", () => {
    setMobileCasesOpen(!$("#caseNav").classList.contains("is-open"));
  });
  $("#caseNavScrim").addEventListener("click", () => setMobileCasesOpen(false));

  // Keyboard shortcut for search
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      $("#globalSearch").focus();
    }
    if (e.key === "Escape") {
      $("#globalSearch").blur();
      closeModal();
      setMobileCasesOpen(false);
    }
  });

  // Filters
  ["filterProvince", "filterStatus", "filterSort"].forEach((id) => {
    $(`#${id}`).addEventListener("change", filterCases);
  });

  // Lead filters
  ["leadFilterConfidence", "leadFilterSource", "leadFilterReview"].forEach((id) => {
    $(`#${id}`).addEventListener("change", renderLeads);
  });

  // Tabs
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // Back to overview
  $("#backToOverview").addEventListener("click", () => {
    state.selectedCaseId = null;
    $$(".case-card").forEach((el) => el.classList.remove("is-selected"));
    showView("overview");
    // Refresh overview map
    setTimeout(() => {
      if (state.maps.overview) state.maps.overview.invalidateSize();
    }, 100);
  });

  // Run investigation
  $("#runInvestigationBtn").addEventListener("click", runInvestigation);

  // Sync
  $("#syncBtn").addEventListener("click", syncCases);

  // Resource pack
  $("#viewResourcePackBtn").addEventListener("click", async () => {
    if (!state.apiOnline || !state.selectedCaseId) {
      showToast("Backend required for resource pack", "warning");
      return;
    }
    const data = await api(`/api/investigations/cases/${state.selectedCaseId}/resource-pack`);
    if (data) {
      showModal(`
        <h2 style="font-family:var(--font-display);font-size:22px;font-weight:800;margin-bottom:16px;color:var(--amber)">
          OSINT Resource Pack
        </h2>
        <pre style="font-family:var(--font-mono);font-size:12px;white-space:pre-wrap;overflow-wrap:break-word;word-break:break-word;color:var(--text-secondary);line-height:1.6;max-height:60vh;overflow-y:auto">
${escHtml(JSON.stringify(data, null, 2))}
        </pre>`);
    }
  });

  // Modal close
  $("#modalClose").addEventListener("click", closeModal);
  $("#modalOverlay").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
  });

  // Copy brief button
  $("#copyBriefBtn")?.addEventListener("click", () => {
    const text = $("#briefOutput")?.textContent;
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      showToast("Authority brief copied to clipboard", "success");
    }).catch(() => {
      showToast("Failed to copy — use manual selection", "warning");
    });
  });

  console.log("[MAAT] Intelligence Console booted ✓");
}

// Boot when DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
