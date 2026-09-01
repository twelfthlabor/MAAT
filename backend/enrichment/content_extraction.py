"""Article content extraction and sighting intelligence extraction.

Given a URL, fetches the page content and extracts structured intelligence:
  - Sighting locations (cities, addresses, landmarks)
  - Physical descriptions (hair, clothing, build)
  - Dates and times mentioned
  - Quoted witness statements
  - Named persons (witnesses, family, etc.)
  - Vehicle descriptions
  - Direction of travel

This is the key enrichment step that turns a news headline into actionable
intelligence. Without it, the project just aggregates headlines.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape

import httpx

from shared.utils.text import normalize_whitespace


@dataclass
class SightingDetail:
    """Extracted sighting intelligence from an article."""
    sighting_location: str | None = None
    sighting_date: str | None = None
    physical_description: list[str] = field(default_factory=list)
    clothing_description: list[str] = field(default_factory=list)
    vehicle_description: str | None = None
    direction_of_travel: str | None = None
    witness_quotes: list[str] = field(default_factory=list)
    mentioned_persons: list[str] = field(default_factory=list)
    mentioned_locations: list[str] = field(default_factory=list)
    reward_amount: str | None = None
    case_number: str | None = None
    contact_info: list[str] = field(default_factory=list)
    licence_plate: str | None = None
    key_facts: list[str] = field(default_factory=list)
    raw_text: str = ""


# ── Canadian location patterns ──────────────────────────────────────
_CANADIAN_CITIES = {
    # Saskatchewan
    "yorkton", "regina", "saskatoon", "prince albert", "moose jaw",
    "north battleford", "swift current", "estevan", "weyburn", "melfort",
    "humboldt", "meadow lake", "martensville", "warman", "lloydminster",
    # Alberta
    "calgary", "edmonton", "red deer", "lethbridge", "medicine hat",
    "grande prairie", "airdrie", "spruce grove", "st. albert", "okotoks",
    "fort mcmurray", "banff", "canmore",
    # British Columbia
    "vancouver", "victoria", "surrey", "burnaby", "kelowna", "kamloops",
    "nanaimo", "prince george", "chilliwack", "abbotsford", "langley",
    # Ontario
    "toronto", "ottawa", "mississauga", "brampton", "hamilton", "london",
    "markham", "vaughan", "kitchener", "windsor", "thunder bay", "sudbury",
    # Quebec
    "montreal", "quebec city", "laval", "gatineau", "sherbrooke",
    # Manitoba
    "winnipeg", "brandon", "steinbach", "thompson", "portage la prairie",
    # Atlantic
    "halifax", "fredericton", "saint john", "moncton", "charlottetown",
    "st. john's",
    # Territories + border cities
    "whitehorse", "yellowknife", "iqaluit",
    # US border cities relevant to missing persons
    "minot", "fargo", "great falls", "seattle", "bellingham", "portal",
    # Small towns frequently mentioned in missing persons cases (Alberta/SK)
    "stettler", "coronation", "consort", "veteran", "hoosier",
    "kindersley", "rosetown", "biggar", "unity", "kerrobert",
    "hanna", "drumheller", "oyen", "provost", "wainwright",
    "lac la biche", "athabasca", "slave lake", "high level",
    "wetaskiwin", "camrose", "ponoka", "lacombe", "innisfail",
    "olds", "didsbury", "sundre", "rocky mountain house",
    "st. paul", "bonnyville", "cold lake", "vegreville",
    "brooks", "taber", "coaldale", "pincher creek", "claresholm",
    "saddle lake", "sault ste. marie", "peterborough", "barrie",
    "brantford", "lower sackville", "charters settlement",
    "st. albert",
}

_HEIGHT_RE = re.compile(
    r"\b(\d['\u2019]\s*\d{1,2}[\"″]?|\d\s*(?:foot|feet|ft)\s*\d{1,2}(?:\s*(?:inch|inches|in))?)\b",
    re.IGNORECASE,
)
_WEIGHT_RE = re.compile(
    r"\b(\d{2,3}\s*(?:lbs?|pounds?|kg|kilograms?))\b",
    re.IGNORECASE,
)
_REWARD_RE = re.compile(
    r"\$\s?[\d,]{3,}(?:\.\d{2})?(?:\s*reward)?|\breward\s+(?:of\s+)?\$\s?[\d,]{3,}",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"\b(?:1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"
)
_CASE_NUM_RE = re.compile(
    r"\b(?:case\s*(?:number|#|no\.?)?\s*[:.]?\s*(\d[\d\-/]+\d))\b",
    re.IGNORECASE,
)
_DATE_PATTERNS = [
    re.compile(r"\b(?:on\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b", re.IGNORECASE),
    re.compile(r"\b(?:on\s+)?\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b", re.IGNORECASE),
    re.compile(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b"),
]

_DIRECTION_RE = re.compile(
    r"\b(?:heading|travelling|traveling|headed|moving|driving|walking|going)\s+"
    r"(?:towards?|to|into|from|north|south|east|west|northbound|southbound|eastbound|westbound)\b",
    re.IGNORECASE,
)

_CLOTHING_KEYWORDS = {
    "wearing", "wore", "dressed in", "clothing", "jacket", "hoodie",
    "jeans", "pants", "shoes", "boots", "sneakers", "hat", "cap",
    "backpack", "bag", "scarf", "coat", "sweater", "t-shirt", "shirt",
}

_VEHICLE_RE = re.compile(
    r"\b((?:19|20)\d{2}\s+(?:[A-Za-z]+[\s-]+){1,3}(?:sedan|suv|truck|van|car|pickup|coupe|hatchback|xle|xse|le|se))\b"
    r"|\b((?:grey|gray|white|black|red|blue|green|silver|brown|beige|dark|light)\s+"
    r"(?:Toyota|Honda|Ford|Chevrolet|Chevy|Dodge|Nissan|Hyundai|Kia|GMC|Jeep|Subaru|Volkswagen|VW|BMW|Mercedes|Audi)"
    r"(?:\s+[A-Za-z0-9-]+){1,3})"
    r"|\b(?:driving|seen in|entered|operating)\s+a\s+(\w+[\s-]+\w+(?:\s+\w+){0,2})\b",
    re.IGNORECASE,
)

_LICENCE_PLATE_RE = re.compile(
    r"\b(?:licence|license)\s+(?:plate)?\s*(?:number|#|no\.?)?\s*[:.]?\s*([A-Z]{2,4}[\s-]?\d{2,4}[A-Z]?\d?)\b"
    r"|\b(?:plate)\s+(?:number|#|no\.?)?\s*[:.]?\s*([A-Z]{2,4}[\s-]?\d{2,4}[A-Z]?\d?)\b"
    r"|\b([A-Z]{3}\s?\d{3,4})\b",
    re.IGNORECASE,
)

# Years that look like licence plates but aren't
_YEAR_LIKE = re.compile(r"^[A-Z]{3}\s?(?:19|20)\d{2}$", re.IGNORECASE)

_QUOTE_RE = re.compile(
    r'["\u201c]([^"\u201d]{20,300}?)["\u201d]',
)

_SIGHTING_PHRASES = re.compile(
    r"\b(?:spotted|seen|sighted|observed|reported seeing|possibly spotted|"
    r"believed to (?:be|have been) (?:seen|spotted)|"
    r"last seen|was seen|may have been seen)\b",
    re.IGNORECASE,
)


def _strip_html(html: str) -> str:
    """Convert HTML to plain text."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_article_body(html: str) -> str:
    """Try to extract the main article body from HTML."""
    # Try common article containers
    for pattern in [
        r'<article[^>]*>(.*?)</article>',
        r'class="(?:article-body|story-body|entry-content|post-content|article-content)[^"]*"[^>]*>(.*?)</(?:div|section)',
        r'<main[^>]*>(.*?)</main>',
    ]:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _strip_html(match.group(1))

    # Fallback: strip all HTML from body
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_match:
        return _strip_html(body_match.group(1))

    return _strip_html(html)


def _extract_sentences_near_keyword(text: str, keywords: list[str], window: int = 200) -> list[str]:
    """Extract text fragments near keyword mentions, snapped to sentence boundaries."""
    results = []
    text_lower = text.lower()
    for kw in keywords:
        idx = text_lower.find(kw.lower())
        while idx != -1:
            start = max(0, idx - window)
            end = min(len(text), idx + len(kw) + window)
            # Snap to sentence boundaries
            # Find sentence start: look for '. ' or start of text
            sentence_start = text.rfind('. ', start, idx)
            if sentence_start != -1:
                start = sentence_start + 2  # skip '. '
            else:
                # Try newline boundary
                nl = text.rfind('\n', start, idx)
                if nl != -1:
                    start = nl + 1
            # Find sentence end: look for '. ' or end of text
            sentence_end = text.find('. ', idx + len(kw), end + 50)
            if sentence_end != -1:
                end = sentence_end + 1  # include the period
            else:
                nl = text.find('\n', idx + len(kw), end + 50)
                if nl != -1:
                    end = nl
            fragment = text[start:end].strip()
            if fragment and fragment not in results:
                results.append(fragment)
            idx = text_lower.find(kw.lower(), idx + 1)
            if len(results) >= 5:
                break
    return results


def extract_sighting_details(text: str, subject_name: str = "") -> SightingDetail:
    """Extract structured sighting intelligence from article text."""
    detail = SightingDetail(raw_text=text[:3000])

    text_lower = text.lower()
    subject_name_lower = subject_name.lower() if subject_name else ""

    # ── Sighting locations ──
    mentioned_locations = []
    subject_last_name = subject_name_lower.split()[-1] if subject_name_lower else ""
    for city in _CANADIAN_CITIES:
        if city in text_lower:
            # Get sentence context around city mention
            idx = text_lower.find(city)
            context_window = text_lower[max(0, idx - 200):idx + len(city) + 200]
            if _SIGHTING_PHRASES.search(text_lower[max(0, idx - 100):idx + len(city) + 100]):
                # On multi-case pages, only set sighting_location if the subject's name
                # is within the same context window as the city + sighting phrase
                if not subject_last_name or subject_last_name in context_window:
                    detail.sighting_location = city.title()
            mentioned_locations.append(city.title())
    detail.mentioned_locations = list(set(mentioned_locations))

    # ── Physical description ──
    heights = _HEIGHT_RE.findall(text)
    weights = _WEIGHT_RE.findall(text)
    # Filter out unrealistic weights (e.g. pet weights like "15 lb")
    realistic_weights = []
    for w in weights:
        digits = re.search(r"(\d+)", w)
        if digits:
            val = int(digits.group(1))
            # Convert kg to lbs for comparison
            if "kg" in w.lower():
                val = int(val * 2.2)
            if val >= 50:  # No adult weighs under 50 lbs
                realistic_weights.append(w)
    detail.physical_description = [f"Height: {h}" for h in heights[:2]] + [f"Weight: {w}" for w in realistic_weights[:2]]

    # Hair / eye color
    for pattern_str in [
        r"\b((?:brown|black|blonde|blond|red|auburn|grey|gray|dark|light|strawberry\s+blonde)\s+hair)\b",
        r"\b((?:brown|blue|green|hazel|grey|gray|dark)\s+eyes?)\b",
    ]:
        for m in re.finditer(pattern_str, text, re.IGNORECASE):
            detail.physical_description.append(m.group(1).strip())

    # ── Clothing ──
    for kw in _CLOTHING_KEYWORDS:
        fragments = _extract_sentences_near_keyword(text, [kw], window=120)
        for frag in fragments[:1]:
            # Only include clothing info if it's reasonably related to the case
            frag_lower = frag.lower()
            # The clothing keyword must actually appear in the captured fragment
            if kw.lower() not in frag_lower:
                continue
            if len(frag) >= 15 and (
                not subject_name_lower
                or subject_name_lower in frag_lower
                or any(p in frag_lower for p in ["missing", "last seen", "was wearing", "wore", "dressed"])
            ):
                detail.clothing_description.append(frag)
    # Deduplicate
    detail.clothing_description = list(dict.fromkeys(detail.clothing_description))[:5]

    # ── Vehicle ──
    vehicle_match = _VEHICLE_RE.search(text)
    if vehicle_match:
        detail.vehicle_description = (vehicle_match.group(1) or vehicle_match.group(2) or "").strip()

    # ── Licence plate ──
    plate_match = _LICENCE_PLATE_RE.search(text)
    if plate_match:
        plate = (plate_match.group(1) or plate_match.group(2) or plate_match.group(3) or "").strip().upper()
        if len(plate) >= 5 and not _YEAR_LIKE.match(plate):
            detail.licence_plate = plate

    # ── Direction of travel ──
    direction_match = _DIRECTION_RE.search(text)
    if direction_match:
        start = max(0, direction_match.start() - 50)
        end = min(len(text), direction_match.end() + 100)
        direction_context = text[max(0, direction_match.start() - 150):direction_match.end() + 150].lower()
        # Only include if near missing-person content
        if (subject_name_lower and subject_name_lower.split()[-1] in direction_context) or \
           any(kw in direction_context for kw in ["missing", "last seen", "search", "rcmp", "police"]):
            detail.direction_of_travel = text[start:end].strip()

    # ── Dates ──
    for date_re in _DATE_PATTERNS:
        date_match = date_re.search(text)
        if date_match and not detail.sighting_date:
            detail.sighting_date = date_match.group(0).strip()

    # ── Witness quotes ──
    _QUOTE_CONTEXT_KEYWORDS = {"missing", "search", "seen", "spotted", "last", "concern", "help", "found",
                                "police", "rcmp", "investigation", "family", "appeal", "public"}
    for m in _QUOTE_RE.finditer(text):
        quote = m.group(1).strip()
        # Get surrounding context (100 chars before and after the quote)
        ctx_start = max(0, m.start() - 100)
        ctx_end = min(len(text), m.end() + 100)
        context = text[ctx_start:ctx_end].lower()
        # Filter: quote must be near missing-person-related content
        context_relevant = any(kw in context for kw in _QUOTE_CONTEXT_KEYWORDS)
        # Also check the subject name if provided
        name_relevant = subject_name_lower and subject_name_lower.split()[-1] in context
        if (len(quote) >= 20
            and (context_relevant or name_relevant)
            and not quote.startswith("data-")
            and not re.search(r"[^\x00-\x7F]{5,}", quote)  # Skip non-ASCII heavy strings
            and any(c.isalpha() for c in quote[:10])  # Must start with letters
        ):
            detail.witness_quotes.append(quote)
    detail.witness_quotes = detail.witness_quotes[:5]

    # ── Reward ──
    reward_match = _REWARD_RE.search(text)
    if reward_match:
        detail.reward_amount = reward_match.group(0).strip()

    # ── Case number ──
    case_match = _CASE_NUM_RE.search(text)
    if case_match:
        detail.case_number = case_match.group(1)

    # ── Contact info ──
    detail.contact_info = list(set(_PHONE_RE.findall(text)))[:5]

    # ── Key facts (sighting-related sentences) ──
    sighting_sentences = _extract_sentences_near_keyword(
        text,
        ["spotted", "seen", "sighted", "last seen", "possibly spotted",
         "believed to be", "reported seeing", "was seen at", "was seen in"],
        window=150,
    )
    detail.key_facts = sighting_sentences[:8]

    return detail


async def fetch_and_extract(
    url: str,
    subject_name: str = "",
    timeout: float = 15.0,
) -> SightingDetail | None:
    """Fetch a URL and extract sighting intelligence from its content.

    Returns None if the URL cannot be fetched or content is too short.
    """
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; OSINT-Research-Tool/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None

            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                return None

            html = resp.text
            if len(html) < 500:
                return None

            article_text = _extract_article_body(html)
            if len(article_text) < 100:
                return None

            return extract_sighting_details(article_text, subject_name)

    except (httpx.HTTPError, httpx.TimeoutException, Exception):
        return None


async def resolve_google_news_url(title: str, subject_name: str = "") -> str | None:
    """Resolve a Google News RSS redirect URL to the actual article URL.

    Google News RSS URLs are JavaScript redirects that can't be followed by
    httpx. Instead, we search for the article title via DuckDuckGo to find
    the actual URL.
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return None

    # Build a focused search query — article title + subject name
    search_query = title
    if subject_name and subject_name.lower() not in title.lower():
        search_query = f"{subject_name} {title}"

    def _search() -> list[dict]:
        with DDGS() as ddg:
            return list(ddg.text(search_query, max_results=3))

    try:
        results = await asyncio.to_thread(_search)
        for r in results:
            url = r.get("href", "")
            if not url:
                continue
            # Skip other Google News URLs, social media, or aggregators
            if "news.google.com" in url or "google.com/amp" in url:
                continue
            # Return the first real news article URL
            return url
    except Exception:
        pass
    return None


def summarize_sighting(detail: SightingDetail) -> str:
    """Create a human-readable summary of extracted sighting intelligence."""
    parts = []

    if detail.sighting_location:
        parts.append(f"SIGHTING LOCATION: {detail.sighting_location}")
    if detail.sighting_date:
        parts.append(f"DATE: {detail.sighting_date}")
    if detail.physical_description:
        parts.append(f"PHYSICAL: {'; '.join(detail.physical_description)}")
    if detail.clothing_description:
        parts.append(f"CLOTHING: {detail.clothing_description[0][:150]}")
    if detail.vehicle_description:
        parts.append(f"VEHICLE: {detail.vehicle_description}")
    if detail.licence_plate:
        parts.append(f"PLATE: {detail.licence_plate}")
    if detail.direction_of_travel:
        parts.append(f"DIRECTION: {detail.direction_of_travel}")
    if detail.mentioned_locations:
        parts.append(f"LOCATIONS MENTIONED: {', '.join(detail.mentioned_locations[:5])}")
    if detail.reward_amount:
        parts.append(f"REWARD: {detail.reward_amount}")
    if detail.contact_info:
        parts.append(f"CONTACT: {', '.join(detail.contact_info[:3])}")
    if detail.key_facts:
        parts.append(f"KEY FACT: {detail.key_facts[0][:200]}")

    return " | ".join(parts) if parts else ""
