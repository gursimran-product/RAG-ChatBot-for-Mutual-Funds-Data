"""
Embedding layer.

Model      : BAAI/bge-small-en-v1.5 (local, via sentence-transformers) — 384 dims
Inference  : fully local, no API key, no cost
Change-gate: chunks whose ID already exists in ChromaDB are skipped
             (deterministic ID = sha256(text), so same ID ↔ same text ↔ same vector)
"""

import logging
from collections import defaultdict

from src.embedding import embed_documents

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Change-gated filtering
# ---------------------------------------------------------------------------

def filter_new_chunks(chunks: list) -> list:
    """
    Return only chunks that do not already exist in ChromaDB.

    Since chunk IDs are deterministic (sha256 of text), a chunk ID already
    present in ChromaDB means the text — and therefore the vector — is
    identical. We skip those to avoid redundant inference.
    """
    from src.ingestion.vector_store import get_existing_ids_for_scheme

    by_scheme: dict[str, list] = defaultdict(list)
    for chunk in chunks:
        by_scheme[chunk.metadata["scheme_name"]].append(chunk)

    new_chunks: list = []
    for scheme_name, scheme_chunks in by_scheme.items():
        existing_ids = get_existing_ids_for_scheme(scheme_name)
        new = [c for c in scheme_chunks if c.id not in existing_ids]
        skipped = len(scheme_chunks) - len(new)
        if skipped:
            logger.info(
                "%s: %d/%d chunk(s) unchanged — skipping re-embed",
                scheme_name, skipped, len(scheme_chunks),
            )
        new_chunks.extend(new)

    return new_chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def batch_embed(chunks: list) -> list[list[float]]:
    """
    Encode all chunks locally using BAAI/bge-small-en-v1.5.
    Returns a list of 384-dim vectors, order-aligned with input chunks.
    """
    texts = [c.text for c in chunks]
    logger.info("Encoding %d chunk(s) with bge-small-en-v1.5 (local)", len(texts))

    vectors = embed_documents(texts)

    logger.info(
        "Encoding complete — %d vectors, %d dims each",
        len(vectors), len(vectors[0]) if vectors else 0,
    )
    return vectors
