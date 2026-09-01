"""Environment-driven settings and feature flags."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from shared.constants.flags import FEATURE_FLAGS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    """Runtime settings."""

    project_root: Path = PROJECT_ROOT
    data_dir: Path = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
    docs_dir: Path = PROJECT_ROOT / "docs"
    docs_data_dir: Path = docs_dir / "data"
    reference_dir: Path = data_dir / "reference"
    cache_dir: Path = data_dir / "cache"
    export_dir: Path = data_dir / "exports"
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'data' / 'db.sqlite'}")
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    debug: bool = _bool_env("DEBUG", False)
    sync_interval_minutes: int = int(os.getenv("SYNC_INTERVAL_MINUTES", "60"))
    public_export_path: Path = docs_data_dir / "public-cases.json"
    mcsc_feature_server_url: str = os.getenv(
        "MCSC_FEATURE_SERVER_URL",
        "https://services.arcgis.com/Sv9ZXFjH5h1fYAaI/arcgis/rest/services/"
        "Missing_Children_Cases_View_Master/FeatureServer/0",
    )
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "25"))
    connector_timeout_seconds: float = float(os.getenv("CONNECTOR_TIMEOUT_SECONDS", "20"))
    connector_delay_seconds: float = float(os.getenv("CONNECTOR_DELAY_SECONDS", "0.4"))
    connector_concurrency: int = int(os.getenv("CONNECTOR_CONCURRENCY", "4"))
    article_fetch_timeout_seconds: float = float(os.getenv("ARTICLE_FETCH_TIMEOUT_SECONDS", "12"))
    max_enrichment_articles: int = int(os.getenv("MAX_ENRICHMENT_ARTICLES", "12"))
    enrichment_concurrency: int = int(os.getenv("ENRICHMENT_CONCURRENCY", "4"))
    enable_location_geocoding: bool = _bool_env("ENABLE_LOCATION_GEOCODING", True)
    geocoder_url: str = os.getenv("GEOCODER_URL", "https://nominatim.openstreetmap.org")
    geocoder_user_agent: str = os.getenv(
        "GEOCODER_USER_AGENT",
        "MAAT/1.0 (https://github.com/consistentlearningguy/MAAT)",
    )
    geocoder_min_interval_seconds: float = float(os.getenv("GEOCODER_MIN_INTERVAL_SECONDS", "1.1"))
    public_dashboard_base_url: str = os.getenv("PUBLIC_DASHBOARD_BASE_URL", "")
    searxng_url: str | None = os.getenv("SEARXNG_URL")
    gdelt_doc_api_url: str = os.getenv(
        "GDELT_DOC_API_URL",
        "https://api.gdeltproject.org/api/v2/doc/doc",
    )
    spiderfoot_url: str | None = os.getenv("SPIDERFOOT_URL")
    theharvester_binary: str | None = os.getenv("THEHARVESTER_BINARY")
    reconng_binary: str | None = os.getenv("RECONNG_BINARY")
    onionsearch_binary: str | None = os.getenv("ONIONSEARCH_BINARY")
    sherlock_binary: str | None = os.getenv("SHERLOCK_BINARY")
    sherlock_timeout_seconds: float = float(os.getenv("SHERLOCK_TIMEOUT_SECONDS", "75"))
    sherlock_site_timeout_seconds: int = int(os.getenv("SHERLOCK_SITE_TIMEOUT_SECONDS", "5"))
    sherlock_max_usernames: int = int(os.getenv("SHERLOCK_MAX_USERNAMES", "4"))
    whatsmyname_data_url: str = os.getenv(
        "WHATSMYNAME_DATA_URL",
        "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json",
    )
    whatsmyname_max_sites: int = int(os.getenv("WHATSMYNAME_MAX_SITES", "48"))
    whatsmyname_max_usernames: int = int(os.getenv("WHATSMYNAME_MAX_USERNAMES", "2"))
    whatsmyname_timeout_seconds: float = float(os.getenv("WHATSMYNAME_TIMEOUT_SECONDS", "6"))
    arquivo_textsearch_url: str = os.getenv(
        "ARQUIVO_TEXTSEARCH_URL",
        "https://arquivo.pt/textsearch",
    )
    arquivo_timeout_seconds: float = float(os.getenv("ARQUIVO_TIMEOUT_SECONDS", "28"))
    arquivo_max_results: int = int(os.getenv("ARQUIVO_MAX_RESULTS", "8"))
    tor_proxy_url: str | None = os.getenv("TOR_PROXY_URL")
    ahmia_search_url: str = os.getenv("AHMIA_SEARCH_URL", "https://ahmia.fi/search/")
    reverse_image_provider_mode: str | None = os.getenv("REVERSE_IMAGE_PROVIDER_MODE")
    reverse_image_provider_url: str | None = os.getenv("REVERSE_IMAGE_PROVIDER_URL")
    reverse_image_mock_file: Path = Path(
        os.getenv(
            "REVERSE_IMAGE_MOCK_FILE",
            str(PROJECT_ROOT / "data" / "reference" / "reverse_image_mock_results.json"),
        )
    )
    enable_mock_connector: bool = _bool_env("ENABLE_MOCK_CONNECTOR", False)

    enable_investigator_mode: bool = _bool_env("ENABLE_INVESTIGATOR_MODE", True)
    enable_clear_web_connectors: bool = _bool_env("ENABLE_CLEAR_WEB_CONNECTORS", True)
    enable_public_profile_checks: bool = _bool_env("ENABLE_PUBLIC_PROFILE_CHECKS", True)
    enable_reverse_image_hooks: bool = _bool_env("ENABLE_REVERSE_IMAGE_HOOKS", True)
    enable_local_face_workflow: bool = _bool_env("ENABLE_LOCAL_FACE_WORKFLOW", False)
    enable_dark_web_connectors: bool = _bool_env("ENABLE_DARK_WEB_CONNECTORS", False)
    enable_experimental_connectors: bool = _bool_env("ENABLE_EXPERIMENTAL_CONNECTORS", False)

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.docs_data_dir,
            self.reference_dir,
            self.cache_dir,
            self.export_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def feature_flags(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in FEATURE_FLAGS}


settings = Settings()
settings.ensure_directories()

