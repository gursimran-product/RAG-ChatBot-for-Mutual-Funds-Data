"""
Retriever — Component 5.

Embeds the user query with BAAI/bge-small-en-v1.5 (local, no API key) then
performs cosine similarity search in ChromaDB (collection: mutual_fund_faq).

Top-K = 4.  If the query mentions a specific scheme, a metadata filter
is applied to restrict results to that scheme's chunks.
"""

import logging
import re

from src.embedding import embed_query as _embed_query
from src.ingestion.vector_store import get_collection

logger = logging.getLogger(__name__)

TOP_K = 4

# Maps scheme_name → keyword patterns that hint at that scheme in the query
_SCHEME_HINTS: dict[str, list[re.Pattern]] = {
    "sbi-gold-fund": [re.compile(p, re.IGNORECASE) for p in [
        r"\bgold fund\b", r"\bsbi gold\b", r"\bgold etf\b",
    ]],
    "sbi-psu-fund": [re.compile(p, re.IGNORECASE) for p in [
        r"\bpsu fund\b", r"\bsbi psu\b", r"\bpublic sector\b", r"\bpsu\b",
    ]],
}


def _detect_scheme(query: str) -> str | None:
    """Return scheme_name if query clearly targets one scheme, else None."""
    for scheme_name, patterns in _SCHEME_HINTS.items():
        if any(p.search(query) for p in patterns):
            logger.info("Scheme filter detected: %s", scheme_name)
            return scheme_name
    return None


def retrieve(query: str) -> list[dict]:
    """
    Returns a list of up to TOP_K result dicts, each with:
      { "text": str, "metadata": dict, "distance": float }
    Ordered by relevance (lowest cosine distance first).
    """
    # BGE query instruction prefix is applied inside embed_query()
    query_vector = _embed_query(query)

    collection = get_collection()
    scheme_filter = _detect_scheme(query)

    kwargs: dict = {
        "query_embeddings": [query_vector],
        "n_results": TOP_K,
        "include": ["documents", "metadatas", "distances"],
    }
    if scheme_filter:
        kwargs["where"] = {"scheme_name": scheme_filter}

    results = collection.query(**kwargs)

    hits = []
    for text, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        hits.append({"text": text, "metadata": meta, "distance": dist})

    logger.info(
        "Retrieved %d chunk(s) for query (scheme_filter=%s)",
        len(hits), scheme_filter,
    )
    return hits
