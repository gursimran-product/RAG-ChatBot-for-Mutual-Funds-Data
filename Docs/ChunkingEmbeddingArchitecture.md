# Chunking & Embedding Architecture

## Overview

This document details how cleaned HTML text from the two Groww fund pages is split into chunks and encoded as vector embeddings before being stored in Chroma Cloud. This pipeline runs inside the GitHub Actions daily ingest job, immediately after the Scraping Service produces its output documents.

---

## Pipeline Position

```
[Scraping Service]
       │
       │  (text, metadata) per URL
       ▼
[Chunker]  ──▶  [Embedder]  ──▶  [Vector Store Upsert]
```

---

## Stage 1 — Chunking

### Goal

Split each fund page's cleaned text into small, self-contained pieces so that a single retrieved chunk answers one factual question without carrying irrelevant surrounding text.

### Input

A document object per URL:

```python
{
  "text": "<cleaned full-page text>",
  "metadata": {
    "source_url": "https://groww.in/mutual-funds/sbi-gold-fund-direct-growth",
    "scheme_name": "sbi-gold-fund",
    "amc_name":    "SBI Mutual Fund",
    "category":    "Gold / Commodity",
    "fetch_date":  "2026-04-25T03:15:00Z"
  }
}
```

### Strategy: Two-pass chunking

Groww fund pages have two structurally distinct regions:

| Region | Content | Chunking approach |
|---|---|---|
| **Key facts table** | Expense ratio, exit load, min SIP, benchmark, riskometer, lock-in | Row-level splitting — one chunk per key-value pair |
| **Prose sections** | Fund overview, about the fund, investment objective | Recursive character splitting |

#### Pass 1 — Table row extraction

The Scraping Service preserves table rows as `KEY :: VALUE` lines, e.g.:

```
Expense Ratio :: 0.18%
Exit Load :: 1% if redeemed within 1 year
Minimum SIP Amount :: ₹500
Benchmark :: Domestic Prices of Gold
Riskometer :: Very High Risk
ELSS Lock-in :: N/A
```

Each line becomes its own chunk. This guarantees that a query for "expense ratio" retrieves a chunk that contains *only* the expense ratio line, not unrelated text.

**Chunk example:**
```python
{
  "text": "Expense Ratio :: 0.18%",
  "metadata": { ..., "chunk_type": "table_row", "field": "expense_ratio" }
}
```

#### Pass 2 — Recursive character splitting on prose

Applied to the fund overview and about-the-fund paragraphs using LangChain's `RecursiveCharacterTextSplitter`.

| Parameter | Value | Reason |
|---|---|---|
| `chunk_size` | 400 tokens | Fits comfortably in the LLM prompt alongside other chunks |
| `chunk_overlap` | 50 tokens | Prevents a sentence from being cut exactly at a chunk boundary and losing context |
| `separators` | `["\n\n", "\n", ". ", " "]` | Tries to break at paragraph → sentence → word boundaries in that order |
| `length_function` | `tiktoken` (`cl100k_base`) | Counts tokens not characters, so chunk_size is accurate for the LLM |

**Chunk example:**
```python
{
  "text": "SBI Gold Fund is an open-ended fund of funds scheme that invests in units of SBI Gold ETF. The fund aims to provide returns that closely correspond to the returns provided by SBI Gold ETF.",
  "metadata": { ..., "chunk_type": "prose" }
}
```

### Chunk ID

Each chunk gets a deterministic ID:

```
{scheme_name}::{chunk_type}::{sha256(text)[:8]}
```

Example: `sbi-gold-fund::table_row::a3f2c819`

The same ID is used as the Chroma Cloud document ID, so re-ingestion on the next daily run **upserts** (overwrites) changed chunks and leaves unchanged ones untouched — no duplicate growth in the vector store.

### Chunking output

```
sbi-gold-fund-direct-growth  →  ~12 table-row chunks + ~6–8 prose chunks
sbi-psu-fund-direct-growth   →  ~12 table-row chunks + ~6–8 prose chunks

Total corpus size: ~40–50 chunks
```

---

## Stage 2 — Embedding

### Goal

Convert each chunk's text into a dense vector that captures its semantic meaning, so similar queries and chunks land near each other in vector space.

### Model

| Property | Value |
|---|---|
| **Model** | `BAAI/bge-small-en-v1.5` (local, via `sentence-transformers`) |
| **Dimensions** | 384 |
| **Max input tokens** | 512 tokens |
| **Similarity metric** | Cosine similarity (`normalize_embeddings=True`) |
| **Why this model** | Runs fully locally — no API key, no cost, no network dependency. Strong retrieval quality for English factual Q&A. Loads once and is reused across the ingestion and retrieval paths via a shared singleton. |

**Query instruction:** BGE models are trained with a task-specific prefix for retrieval queries:
```
"Represent this sentence: {query}"
```
This prefix is applied only at query time in the retriever. Chunk texts are encoded without a prefix.

### Batching

All chunks are encoded in a single local inference call per ingestion run:

```python
model = SentenceTransformer("BAAI/bge-small-en-v1.5")
vectors = model.encode(
    [chunk.text for chunk in all_chunks],
    normalize_embeddings=True,
    batch_size=64,
)
```

- No API rate limits, no HTTP overhead.
- `batch_size=64` fits comfortably in CPU RAM for ~50 chunks.

### Change-gated embedding

Before encoding, the pipeline compares each chunk's deterministic ID against the IDs already stored in Chroma Cloud:

```
For each chunk:
  if chunk.id exists in Chroma Cloud:
      skip  → reuse existing vector (same text = same ID = same vector)
  else:
      encode → upsert new vector
```

On a typical day when fund details have not changed, **zero inference calls** are made — the Chroma Cloud IDs act as the cache key.

---

## Stage 3 — Vector Store Upsert

### Store: Chroma Cloud

| Property | Value |
|---|---|
| **Collection** | `mutual_fund_faq` |
| **Persistence** | Chroma Cloud (`chromadb.CloudClient`) — fully managed, no local disk required |
| **Distance metric** | Cosine |
| **Credentials** | `CHROMA_TENANT`, `CHROMA_DATABASE`, `CHROMA_API_KEY` — `.env` locally, GitHub Actions Secrets in CI |

### Upsert logic

```python
collection.upsert(
    ids        = [chunk.id for chunk in new_or_changed_chunks],
    embeddings = [vec for vec in new_embeddings],
    documents  = [chunk.text for chunk in new_or_changed_chunks],
    metadatas  = [chunk.metadata for chunk in new_or_changed_chunks]
)
```

`upsert` inserts if the ID is new, overwrites if the ID already exists — safe to call repeatedly without duplicating data.

---

## Full Data Flow

```
Scraper output (2 documents)
         │
         ▼
┌─────────────────────────────────┐
│         CHUNKER                 │
│                                 │
│  Pass 1: Table row splitter     │
│  ┌──────────────────────────┐   │
│  │ "Expense Ratio :: 0.18%" │──▶│ chunk (table_row)
│  │ "Exit Load :: 1% if ..." │──▶│ chunk (table_row)
│  │  ... ~12 rows per fund   │   │
│  └──────────────────────────┘   │
│                                 │
│  Pass 2: RecursiveCharSplitter  │
│  ┌──────────────────────────┐   │
│  │  Prose paragraphs        │──▶│ chunks (prose, 400 tok, 50 overlap)
│  └──────────────────────────┘   │
└────────────────┬────────────────┘
                 │  ~40-50 chunks with metadata + chunk IDs
                 ▼
┌─────────────────────────────────┐
│        CHANGE DETECTOR          │
│  hash(chunk.text) vs stored     │
│  skip unchanged / flag changed  │
└────────────────┬────────────────┘
                 │  only new/changed chunks
                 ▼
┌─────────────────────────────────┐
│          EMBEDDER               │
│  BAAI/bge-small-en-v1.5 (local) │
│  single batched local call      │
│  384-dim vectors                │
└────────────────┬────────────────┘
                 │  (chunk_id, vector, text, metadata)
                 ▼
┌─────────────────────────────────┐
│      CHROMA CLOUD UPSERT        │
│  collection: mutual_fund_faq    │
│  upsert by chunk_id             │
└─────────────────────────────────┘
```

---

## Cost & Size Estimates

| Metric | Value |
|---|---|
| Total chunks (both funds) | ~40–50 |
| Tokens per chunk (avg) | ~80 tokens |
| Embedding dimensions | 384 (vs 1536 for OpenAI) |
| Cost per full ingest | $0.00 — fully local inference |
| Cost on days with no change | $0.00 |
| ChromaDB storage | < 1 MB on disk |
| Model size on disk | ~133 MB (downloaded once, cached by HuggingFace) |

---

## Implementation Files

| File | Responsibility |
|---|---|
| `scripts/run_ingestion.py` | Entry point called by GitHub Actions; orchestrates scrape → chunk → embed → upsert |
| `src/ingestion/chunker.py` | `TableRowChunker` and `ProseChunker` classes |
| `src/ingestion/embedder.py` | `batch_embed(chunks)` — calls local bge-small model, returns vectors |
| `src/ingestion/change_detector.py` | SHA-256 hash comparison against `data/hashes.json` |
| `src/ingestion/vector_store.py` | Chroma Cloud client wrapper (`CloudClient`); `upsert_chunks()` and `get_collection()` |
| `src/embedding.py` | Shared `SentenceTransformer` singleton; `embed_documents()` and `embed_query()` |
| `corpus/urls.yaml` | Source URL registry with scheme metadata |
