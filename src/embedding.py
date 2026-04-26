"""
Shared embedding model — used by both the ingestion pipeline (embedder.py)
and the online retrieval pipeline (retriever.py).

Model   : BAAI/bge-small-en-v1.5  (local, via sentence-transformers)
Dims    : 384
Cost    : $0 — fully local inference, no API key required
Loading : model is downloaded once to the HuggingFace cache (~133 MB)
          and reused via a module-level singleton.

BGE query instruction:
  BGE models are trained with a task prefix for retrieval queries.
  - Documents (chunks): encoded without any prefix
  - Queries            : encoded with prefix "Represent this sentence: "
"""

import logging
import os

# Suppress Windows symlink warning from huggingface_hub (cosmetic only)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384
QUERY_INSTRUCTION = "Represent this sentence: "

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model: %s", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Embedding model loaded — %d dims", DIMENSIONS)
    return _model


def embed_documents(texts: list[str]) -> list[list[float]]:
    """
    Encode a list of document/chunk texts.
    No prefix — chunks are encoded as-is.
    Returns a list of 384-dim normalised float vectors.
    """
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=64,
        show_progress_bar=False,
    )
    return vectors.tolist()


def embed_query(query: str) -> list[float]:
    """
    Encode a single retrieval query with the BGE instruction prefix.
    Returns a single 384-dim normalised float vector.
    """
    prefixed = f"{QUERY_INSTRUCTION}{query}"
    return embed_documents([prefixed])[0]
