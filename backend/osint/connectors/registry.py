"""Connector registry and feature-flag filtering."""

from __future__ import annotations

from backend.osint.connectors.ahmia import AhmiaConnector
from backend.osint.connectors.arquivo_pt import ArquivoPtConnector
from backend.osint.connectors.bing_news import BingNewsConnector
from backend.osint.connectors.canada_missing import CanadaMissingConnector
from backend.osint.connectors.canadian_news_media import CanadianNewsMediaConnector
from backend.osint.connectors.duckduckgo_html import DuckDuckGoHtmlConnector
from backend.osint.connectors.gdelt import GdeltDocConnector
from backend.osint.connectors.google_news_rss import GoogleNewsRssConnector
from backend.osint.connectors.investigator_toolkit import InvestigatorToolkitConnector
from backend.osint.connectors.mock import MockConnector
from backend.osint.connectors.network_analysis import NetworkAnalysisConnector
from backend.osint.connectors.official_artifacts import OfficialArtifactsConnector
from backend.osint.connectors.onionsearch import OnionSearchConnector
from backend.osint.connectors.rcmp_ncmpur import RcmpCrossRefConnector
from backend.osint.connectors.reconng import ReconNgConnector
from backend.osint.connectors.reddit_search import RedditSearchConnector
from backend.osint.connectors.reverse_image import ReverseImageConnector
from backend.osint.connectors.searxng import SearxngConnector
from backend.osint.connectors.sherlock import SherlockConnector
from backend.osint.connectors.social_profiler import SocialProfilerConnector
from backend.osint.connectors.spiderfoot import SpiderfootConnector
from backend.osint.connectors.theharvester import TheHarvesterConnector
from backend.osint.connectors.wayback_machine import WaybackMachineConnector
from backend.osint.connectors.websleuths import WebsleuthsConnector
from backend.osint.connectors.whatsmyname import WhatsMyNameConnector


def available_connectors() -> list:
    """Return all connector instances."""
    return [
        MockConnector(),
        OfficialArtifactsConnector(),
        CanadaMissingConnector(),
        CanadianNewsMediaConnector(),
        GoogleNewsRssConnector(),
        BingNewsConnector(),
        DuckDuckGoHtmlConnector(),
        RedditSearchConnector(),
        WaybackMachineConnector(),
        ArquivoPtConnector(),
        SearxngConnector(),
        GdeltDocConnector(),
        SpiderfootConnector(),
        TheHarvesterConnector(),
        ReverseImageConnector(),
        ReconNgConnector(),
        AhmiaConnector(),
        OnionSearchConnector(),
        SocialProfilerConnector(),
        SherlockConnector(),
        WhatsMyNameConnector(),
        NetworkAnalysisConnector(),
        WebsleuthsConnector(),
        RcmpCrossRefConnector(),
        InvestigatorToolkitConnector(),
    ]


def enabled_connectors() -> list:
    """Return only enabled connectors."""
    return [connector for connector in available_connectors() if connector.enabled()]

