import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import yaml
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

CORPUS_CONFIG = Path(__file__).parent.parent.parent / "corpus" / "urls.yaml"

# Browser user-agent that Groww accepts without blocking
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class Document(TypedDict):
    text: str
    metadata: dict


# ---------------------------------------------------------------------------
# URL registry
# ---------------------------------------------------------------------------

def load_url_registry(config_path: Path = CORPUS_CONFIG) -> list[dict]:
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["urls"]


# ---------------------------------------------------------------------------
# HTTP + JS rendering
# ---------------------------------------------------------------------------

def fetch_rendered_html(url: str, retries: int = 3) -> str:
    """
    Fetch fully JS-rendered HTML via Playwright headless Chromium.
    Groww pages are Next.js apps — plain requests() won't get fund data.
    """
    last_exc: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=_USER_AGENT)
                page = context.new_page()

                # Block ads/trackers to speed up load
                page.route(
                    "**/{ads,analytics,gtm,hotjar,clarity}**",
                    lambda route: route.abort(),
                )

                try:
                    page.goto(url, wait_until="networkidle", timeout=30_000)
                except PlaywrightTimeout:
                    # Fallback: DOM ready is enough to extract static content
                    logger.warning("networkidle timeout on attempt %d for %s; using domcontentloaded", attempt, url)
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                html = page.content()
                browser.close()
                return html

        except Exception as exc:
            last_exc = exc
            logger.warning("Fetch attempt %d/%d failed for %s: %s", attempt, retries, url, exc)

    raise RuntimeError(f"All {retries} fetch attempts failed for {url}") from last_exc


# ---------------------------------------------------------------------------
# HTML parsing & section extraction
# ---------------------------------------------------------------------------

def extract_sections(html: str) -> dict[str, str]:
    """
    Parse rendered HTML and pull out the sections we care about:
      - key_facts : NAV, Min SIP, Fund Size, Expense Ratio, Rating, Exit Load
      - overview  : investment objective / about-the-fund prose

    Selectors are pinned to Groww's current CSS module class patterns
    (e.g. fundDetails_fundDetailsContainer, investmentObjective_contentSection).
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.select(
        "nav, header, footer, script, style, noscript, "
        "[class*='cookie'], [class*='banner'], [class*='toast'], "
        "[class*='popup'], [class*='modal'], [class*='ad-']"
    ):
        tag.decompose()

    sections: dict[str, str] = {}
    table_rows: dict[str, str] = {}

    # --- Key facts: fundDetails container ---
    # Groww renders NAV / Min SIP / Fund Size / Expense Ratio / Rating in a
    # flex container whose class starts with "fundDetails_fundDetailsContainer".
    # Each direct child holds one label+value pair.
    fd_container = soup.select_one("[class*=fundDetails_fundDetailsContainer]")
    if fd_container:
        for child in fd_container.children:
            if not hasattr(child, "stripped_strings"):
                continue
            texts = [t.strip() for t in child.stripped_strings if t.strip()]
            if len(texts) >= 2:
                label = texts[0]
                value = texts[1]
                # NAV label includes the date, e.g. "NAV: 24 Apr '26" → normalise to "NAV"
                if label.upper().startswith("NAV"):
                    label = "NAV"
                table_rows[label] = value

    # --- Exit load / stamp duty ---
    # Groww puts exit load text in exitLoadStampDutyTax_contentContainer
    exit_container = soup.select_one("[class*=exitLoadStampDutyTax_contentContainer]")
    if exit_container:
        exit_texts = [t.strip() for t in exit_container.stripped_strings if t.strip()]
        for i, text in enumerate(exit_texts):
            if text.lower() == "exit load" and i + 1 < len(exit_texts):
                table_rows["Exit Load"] = exit_texts[i + 1]
                break

    if table_rows:
        sections["key_facts"] = "\n".join(f"{k} :: {v}" for k, v in table_rows.items())

    # --- Overview / investment objective (prose) ---
    overview_el = soup.select_one("[class*=investmentObjective_contentSection]")
    if overview_el:
        txt = overview_el.get_text(" ", strip=True)
        if len(txt) > 60:
            sections["overview"] = txt

    return sections


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_url(entry: dict) -> Document:
    """Scrape a single registry entry and return a Document."""
    url: str = entry["url"]
    logger.info("Scraping: %s", url)

    html = fetch_rendered_html(url)
    sections = extract_sections(html)

    if not sections:
        raise ValueError(f"No content extracted from {url} — selectors may need updating")

    full_text = "\n\n".join(
        f"[{name.upper()}]\n{content}"
        for name, content in sections.items()
        if content.strip()
    )
    cleaned = clean_text(full_text)

    return Document(
        text=cleaned,
        metadata={
            "source_url": url,
            "scheme_name": entry["scheme_name"],
            "display_name": entry["display_name"],
            "amc_name": entry["amc_name"],
            "category": entry["category"],
            "fetch_date": datetime.now(timezone.utc).isoformat(),
            "content_hash": hashlib.sha256(cleaned.encode()).hexdigest(),
        },
    )


def run_scraping_service(config_path: Path = CORPUS_CONFIG) -> list[Document]:
    """Scrape all URLs in the registry. Raises on any failure to fail the pipeline fast."""
    entries = load_url_registry(config_path)
    documents: list[Document] = []

    for entry in entries:
        doc = scrape_url(entry)  # let exception propagate — GitHub Actions will mark the job failed
        logger.info("Done: %s — %d chars", entry["scheme_name"], len(doc["text"]))
        documents.append(doc)

    return documents
