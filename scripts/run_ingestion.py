"""
Daily ingestion pipeline — entry point.
Triggered by GitHub Actions (.github/workflows/daily_ingest.yml) at 09:15 IST.
Can also be run manually: python scripts/run_ingestion.py

Phase 1 — Scraping          : fetch & render Groww HTML pages
Phase 2 — Fund data store   : extract NAV / SIP / AUM / ER / Rating → data/fund_data.json
Phase 3 — Change detection  : SHA-256 doc hash comparison → skip phases 4-5 if unchanged
Phase 4 — Chunking          : two-pass (table_row + prose) → ~40-50 Chunk objects
Phase 5 — Embedding & upsert: change-gated bge-small-en-v1.5 embed → Chroma Cloud upsert + stale cleanup
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows so arrow/Unicode characters in log messages
# don't crash the cp1252 console handler.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from src.ingestion.change_detector import filter_changed, save_hashes
from src.ingestion.chunker import chunk_documents
from src.ingestion.embedder import batch_embed, filter_new_chunks
from src.ingestion.fund_data_store import build_and_save_fund_record
from src.ingestion.scraper import run_scraping_service
from src.ingestion.vector_store import delete_stale_chunks, get_stats, upsert_chunks

# ---------------------------------------------------------------------------
# Logging: stdout (GitHub Actions captures this) + daily rotating file
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"ingest_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=== Ingestion pipeline started ===")

    # ------------------------------------------------------------------
    # Phase 1 — Scraping
    # ------------------------------------------------------------------
    logger.info("--- Phase 1: Scraping ---")
    documents = run_scraping_service()
    logger.info("Scraped %d document(s)", len(documents))

    # ------------------------------------------------------------------
    # Phase 2 — Structured fund data extraction
    # Always runs so NAV (which changes daily) is always up-to-date
    # even on days when the prose content hasn't changed.
    # ------------------------------------------------------------------
    logger.info("--- Phase 2: Fund data extraction ---")
    for doc in documents:
        record = build_and_save_fund_record(doc)
        logger.info(
            "  %s | NAV=%-10s SIP=%-8s AUM=%-12s ER=%-6s Rating=%s",
            record["scheme_name"],
            record["nav"],
            record["minimum_sip"],
            record["fund_size"],
            record["expense_ratio"],
            record["rating"],
        )

    # ------------------------------------------------------------------
    # Phase 3 — Document-level change detection
    # Compares SHA-256 hash of full cleaned text against data/hashes.json.
    # If no document changed, skip chunking + embedding entirely.
    # ------------------------------------------------------------------
    logger.info("--- Phase 3: Change detection ---")
    changed_docs, updated_hashes = filter_changed(documents)
    logger.info(
        "%d/%d document(s) changed and require re-indexing",
        len(changed_docs), len(documents),
    )

    if not changed_docs:
        logger.info("No document content changed. Pipeline complete — $0 embedding cost.")
        return

    # ------------------------------------------------------------------
    # Phase 4 — Chunking
    # Two-pass: table_row chunks (one per KEY :: VALUE line) +
    #           prose chunks (RecursiveCharSplitter 400 tok / 50 overlap)
    # ------------------------------------------------------------------
    logger.info("--- Phase 4: Chunking ---")
    all_chunks = chunk_documents(changed_docs)
    logger.info("Total chunks produced: %d", len(all_chunks))

    # ------------------------------------------------------------------
    # Phase 5 — Change-gated embedding + Chroma Cloud upsert
    # Chunk-level gate: if chunk ID (= sha256 of text) already exists in
    # Chroma Cloud, the vector is identical — skip inference.
    # ------------------------------------------------------------------
    logger.info("--- Phase 5: Embedding & vector store upsert ---")

    new_chunks = filter_new_chunks(all_chunks)
    logger.info(
        "%d/%d chunk(s) are new or changed — will be embedded",
        len(new_chunks), len(all_chunks),
    )

    if new_chunks:
        vectors = batch_embed(new_chunks)
        upsert_chunks(new_chunks, vectors)
    else:
        logger.info("All chunks already up-to-date in Chroma Cloud — no embedding calls made.")

    # Clean up stale chunks for each re-indexed scheme
    current_ids_by_scheme: dict[str, set[str]] = {}
    for chunk in all_chunks:
        scheme = chunk.metadata["scheme_name"]
        current_ids_by_scheme.setdefault(scheme, set()).add(chunk.id)

    total_deleted = 0
    for scheme_name, current_ids in current_ids_by_scheme.items():
        total_deleted += delete_stale_chunks(scheme_name, current_ids)

    if total_deleted:
        logger.info("Cleaned up %d stale chunk(s) from Chroma Cloud", total_deleted)

    # ------------------------------------------------------------------
    # Finalise — persist updated document hashes
    # ------------------------------------------------------------------
    save_hashes(updated_hashes)

    stats = get_stats()
    logger.info(
        "Chroma Cloud '%s' now holds %d chunk(s) total",
        stats["collection"], stats["total_chunks"],
    )
    logger.info("=== Ingestion pipeline complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Pipeline failed")
        sys.exit(1)
