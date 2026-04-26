# API List — Mutual Fund FAQ Assistant

This document lists every external API and internal REST endpoint used in the project.

---

## External APIs

### 1. Groq API
| Property | Detail |
|---|---|
| **Used in** | `src/retrieval/llm.py` |
| **SDK** | `groq` Python SDK |
| **Endpoint** | `https://api.groq.com/openai/v1/chat/completions` (called via SDK) |
| **Auth** | `GROQ_API_KEY` environment variable |
| **Model** | `llama-3.3-70b-versatile` |
| **Purpose** | Generates the final factual answer from retrieved context. Receives the assembled prompt (top-K chunks + source URL + fetch date + user query) and returns a ≤3-sentence response with a citation and "Last updated" footer. |
| **Called when** | Every factual query that passes the classifier and reaches the LLM generation step. Advisory queries are refused before this call is made. |
| **Parameters** | `max_tokens=256`, system message with facts-only guard rails |

---

### 2. Chroma Cloud API
| Property | Detail |
|---|---|
| **Used in** | `src/ingestion/vector_store.py`, `src/retrieval/retriever.py` |
| **SDK** | `chromadb` Python SDK (`chromadb.CloudClient`) |
| **Base URL** | `https://api.trychroma.com` |
| **Auth** | `CHROMA_TENANT`, `CHROMA_DATABASE`, `CHROMA_API_KEY` environment variables |
| **Collection** | `mutual_fund_faq` |
| **Purpose** | Managed vector database that stores chunk embeddings (384-dim bge-small-en-v1.5 vectors) with metadata. Used for both writing (upsert during ingestion) and reading (cosine similarity search during inference). |

**Operations used:**

| Operation | SDK call | When |
|---|---|---|
| Create / get collection | `get_or_create_collection()` | On every pipeline start |
| Check existing chunk IDs | `collection.get(where=...)` | Phase 5 change-gate — skip re-embedding unchanged chunks |
| Upsert vectors | `collection.upsert(ids, embeddings, documents, metadatas)` | Phase 5 — write new/changed chunks |
| Delete stale chunks | `collection.delete(ids=...)` | Phase 5 — remove chunks whose text changed |
| Cosine similarity search | `collection.query(query_embeddings, n_results=4)` | Every factual query — retrieve top-4 chunks |
| Count total chunks | `collection.count()` | End of pipeline — log total stored chunks |

---

### 3. Groww (Web Scraping — not a formal API)
| Property | Detail |
|---|---|
| **Used in** | `src/ingestion/scraper.py` |
| **Tool** | Playwright headless Chromium + BeautifulSoup4 |
| **URLs** | `https://groww.in/mutual-funds/sbi-gold-fund-direct-growth` |
| | `https://groww.in/mutual-funds/sbi-psu-fund-direct-growth` |
| **Auth** | None (public pages) |
| **Purpose** | Fetches fully JS-rendered HTML from Groww fund pages. Extracts key facts table (NAV, expense ratio, exit load, minimum SIP, benchmark, riskometer) and prose sections (fund overview, investment objective). This is the sole data source for the corpus. |
| **Triggered by** | GitHub Actions cron at 09:15 IST daily, or `workflow_dispatch` for manual runs |
| **Retry policy** | 3 attempts; falls back from `networkidle` to `domcontentloaded` on timeout |

---

### 4. HuggingFace Hub (Model Download)
| Property | Detail |
|---|---|
| **Used in** | `src/embedding.py` (via `sentence-transformers`) |
| **URL** | `https://huggingface.co/BAAI/bge-small-en-v1.5` |
| **Auth** | None required for public models |
| **Purpose** | Downloads the `BAAI/bge-small-en-v1.5` embedding model (~133 MB) on first run. Subsequent runs use the cached model from `.hf_cache/`. On GitHub Actions the cache is persisted across runs via `actions/cache@v4` to avoid re-downloading. |
| **Called when** | Once on first run per environment. Never again if cache hit. |

---

## Internal REST API (FastAPI — `app/api.py`)

The FastAPI backend exposes 5 endpoints consumed by the Streamlit UI and any external clients.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/session` | Creates a new conversation thread. Returns a UUID `thread_id` used to isolate this user's history from all other sessions. |
| `POST` | `/chat` | Main chat endpoint. Accepts `thread_id` + `query`. Runs the full inference pipeline (classifier → retriever → context assembler → LLM) and returns the answer, query type, source URL, and fetch date. |
| `GET` | `/session/{thread_id}` | Returns the full conversation history (all turns) for a given thread. Used by the UI to restore chat context. |
| `GET` | `/funds` | Returns the latest structured fund snapshot from `data/fund_data.json` — NAV, minimum SIP, fund size, expense ratio, and rating for each scheme. Used by the Streamlit sidebar. |
| `GET` | `/health` | Liveness check. Returns `{"status": "ok", "active_sessions": N}`. Used to verify the API is running before sending queries. |

### Request / Response shapes

**POST /session**
```json
// Response
{ "thread_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**POST /chat**
```json
// Request
{ "thread_id": "550e8400...", "query": "What is the expense ratio of SBI Gold Fund?" }

// Response
{
  "thread_id": "550e8400...",
  "answer": "The expense ratio of SBI Gold Fund Direct Growth is 0.25%.\nSource: https://groww.in/...\nLast updated from sources: 2026-04-26T03:15:00Z",
  "query_type": "factual",
  "source_url": "https://groww.in/mutual-funds/sbi-gold-fund-direct-growth",
  "fetch_date": "2026-04-26T03:15:00Z"
}
```

**GET /funds**
```json
{
  "sbi-gold-fund": {
    "scheme_name": "sbi-gold-fund",
    "nav": "₹45.77",
    "minimum_sip": "₹500",
    "fund_size": "₹14,997.68 Cr",
    "expense_ratio": "0.25%",
    "rating": "4",
    "source_url": "https://groww.in/mutual-funds/sbi-gold-fund-direct-growth",
    "fetch_date": "2026-04-26T03:15:00Z"
  }
}
```

---

## Environment Variables Summary

| Variable | Used by | Purpose |
|---|---|---|
| `GROQ_API_KEY` | `src/retrieval/llm.py` | Authenticates calls to the Groq API (llama-3.3-70b-versatile) |
| `CHROMA_TENANT` | `src/ingestion/vector_store.py` | Chroma Cloud tenant identifier |
| `CHROMA_DATABASE` | `src/ingestion/vector_store.py` | Chroma Cloud database name |
| `CHROMA_API_KEY` | `src/ingestion/vector_store.py` | Authenticates all Chroma Cloud operations |
| `HF_HOME` | `src/embedding.py` (via HF hub) | Directory for caching the bge-small-en-v1.5 model |
