"""
ChromaDB Cloud client wrapper.

Collection  : mutual_fund_faq
Distance    : cosine
Persistence : Chroma Cloud (https://www.trychroma.com)
              Credentials read from env vars CHROMA_TENANT, CHROMA_DATABASE,
              CHROMA_API_KEY — set these in .env (local) or GitHub Actions
              Secrets (CI).

Stale chunk cleanup:
  When a scheme is re-indexed, any chunk IDs that existed before but are
  absent from the new set are deleted. This prevents accumulation of chunks
  whose text has changed (new ID) while the old ID lingers.
"""

import logging
import os

import chromadb

logger = logging.getLogger(__name__)

COLLECTION_NAME = "mutual_fund_faq"

_client: chromadb.ClientAPI | None = None


# ---------------------------------------------------------------------------
# Client / collection
# ---------------------------------------------------------------------------

def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.CloudClient(
            tenant=os.environ["CHROMA_TENANT"],
            database=os.environ["CHROMA_DATABASE"],
            api_key=os.environ["CHROMA_API_KEY"],
        )
    return _client


def get_collection() -> chromadb.Collection:
    return _get_client().get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_existing_ids_for_scheme(scheme_name: str) -> set[str]:
    """Return the set of chunk IDs currently stored for a given scheme."""
    result = get_collection().get(
        where={"scheme_name": scheme_name},
        include=[],   # IDs only — no embeddings or documents needed
    )
    return set(result["ids"])


def get_stats() -> dict:
    collection = get_collection()
    return {
        "collection": COLLECTION_NAME,
        "total_chunks": collection.count(),
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def upsert_chunks(chunks: list, vectors: list[list[float]]) -> None:
    """
    Upsert chunks and their vectors into ChromaDB.
    insert  → if chunk ID is new
    overwrite → if chunk ID already exists (same text, same vector)
    """
    if not chunks:
        return

    get_collection().upsert(
        ids=[c.id for c in chunks],
        embeddings=vectors,
        documents=[c.text for c in chunks],
        metadatas=[c.metadata for c in chunks],
    )
    logger.info("Upserted %d chunk(s) into '%s'", len(chunks), COLLECTION_NAME)


def delete_stale_chunks(scheme_name: str, current_ids: set[str]) -> int:
    """
    Delete any chunk IDs for a scheme that are no longer in the current
    chunk set (i.e. their source text changed, generating a new ID).
    Returns the number of chunks deleted.
    """
    existing_ids = get_existing_ids_for_scheme(scheme_name)
    stale = existing_ids - current_ids

    if stale:
        get_collection().delete(ids=list(stale))
        logger.info("Deleted %d stale chunk(s) for '%s'", len(stale), scheme_name)

    return len(stale)
