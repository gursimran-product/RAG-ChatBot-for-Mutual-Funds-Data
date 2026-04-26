"""
Shared embedding model — used by both the ingestion pipeline (embedder.py)
and the online retrieval pipeline (retriever.py).

Model   : BAAI/bge-small-en-v1.5  (384 dims, cosine distance)
Cost    : $0 — fully local inference, no API key required

Two execution modes controlled by the USE_HF_API environment variable:

  USE_HF_API=false (default)
    Local mode — loads BAAI/bge-small-en-v1.5 via sentence-transformers.
    Used by the ingestion pipeline on GitHub Actions (ample RAM, no rate limits).

  USE_HF_API=true
    API mode — calls the HuggingFace Inference API instead of loading PyTorch.
    Used by the FastAPI backend on Render's free tier (512 MB RAM — PyTorch
    alone needs ~400 MB, which OOMs the instance before uvicorn can start).
    Set HF_TOKEN for higher rate limits (free HuggingFace account is enough).

BGE query instruction:
  Documents (chunks): encoded without any prefix.
  Queries            : encoded with prefix "Represent this sentence: ".
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384
QUERY_INSTRUCTION = "Represent this sentence: "

_HF_API_URL = f"https://router.huggingface.co/hf-inference/models/{MODEL_NAME}"

# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------

def _use_hf_api() -> bool:
    return os.getenv("USE_HF_API", "false").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# HuggingFace Inference API path (backend / Render)
# ---------------------------------------------------------------------------

def _embed_via_hf_api(texts: list[str]) -> list[list[float]]:
    hf_token = os.getenv("HF_TOKEN", "")
    headers = {"Authorization": f"Bearer {hf_token}"} if hf_token else {}
    resp = requests.post(
        _HF_API_URL,
        headers=headers,
        json={"inputs": texts, "options": {"wait_for_model": True}},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Local sentence-transformers path (GitHub Actions ingestion)
# sentence_transformers is imported LAZILY — importing this module does NOT
# pull in PyTorch. It is only loaded when _get_model() is first called.
# ---------------------------------------------------------------------------

_model = None


def _get_model():
    global _model
    if _model is None:
        # Suppress Windows symlink warning from huggingface_hub (cosmetic only)
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        logger.info("Loading embedding model: %s", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Embedding model loaded — %d dims", DIMENSIONS)
    return _model


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_documents(texts: list[str]) -> list[list[float]]:
    """
    Encode a list of document/chunk texts.
    No prefix — chunks are encoded as-is.
    Returns a list of 384-dim normalised float vectors.
    """
    if not texts:
        return []
    if _use_hf_api():
        logger.debug("Embedding %d text(s) via HF Inference API", len(texts))
        return _embed_via_hf_api(texts)
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
