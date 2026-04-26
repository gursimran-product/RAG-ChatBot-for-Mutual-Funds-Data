# RAG Architecture: Mutual Fund FAQ Assistant

## Overview

This document describes the Retrieval-Augmented Generation (RAG) architecture for the Mutual Fund FAQ Assistant — a facts-only chatbot that answers objective queries about mutual fund schemes using officially sourced documents from AMC, AMFI, and SEBI.

---

## High-Level Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          OFFLINE PIPELINE (Indexing)                     │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  Scheduler  (GitHub Actions – cron: 09:15 IST daily)               │ │
│  └──────────────────────────┬────────────────────────────────────────┘  │
│                             │ triggers                                   │
│                             ▼                                            │
│  Groww URLs                Scraping Service        Vector Store          │
│  ┌─────────────┐          ┌──────────────────┐    ┌──────────────────┐  │
│  │ sbi-gold-   │          │  HTTP Fetcher    │    │                  │  │
│  │ fund-direct │ ───────▶ │  HTML Extractor  │──▶ │  Embeddings      │  │
│  │ -growth     │          │  Text Cleaner    │    │  + Metadata      │  │
│  │ sbi-psu-    │          │  Chunker         │    │  (ChromaDB)      │  │
│  │ fund-direct │          │  Embedder        │    │                  │  │
│  │ -growth     │          └──────────────────┘    └──────────────────┘  │
│  └─────────────┘                                                         │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                          ONLINE PIPELINE (Inference)                     │
│                                                                          │
│  User Query                                                              │
│      │                                                                   │
│      ▼                                                                   │
│  ┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐    │
│  │  Query          │    │  Vector Store    │    │  Context         │    │
│  │  Classifier     │───▶│  Retriever       │───▶│  Assembler       │    │
│  │  (factual vs    │    │  (Top-K chunks)  │    │  (chunks +       │    │
│  │   advisory)     │    │                  │    │   source URLs)   │    │
│  └─────────────────┘    └──────────────────┘    └────────┬─────────┘    │
│          │                                                │              │
│          │ advisory query                                 ▼              │
│          ▼                                      ┌──────────────────┐    │
│  ┌─────────────────┐                            │  LLM (Claude /   │    │
│  │  Refusal        │                            │  GPT-4o)         │    │
│  │  Handler        │                            │  with System     │    │
│  └─────────────────┘                            │  Prompt Guard    │    │
│                                                 └────────┬─────────┘    │
│                                                          │              │
│                                                          ▼              │
│                                                 ┌──────────────────┐    │
│                                                 │  Response        │    │
│                                                 │  Formatter       │    │
│                                                 │  (≤3 sentences,  │    │
│                                                 │   1 citation,    │    │
│                                                 │   last-updated)  │    │
│                                                 └──────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Component Breakdown

### 0. Scheduler

**Purpose:** Trigger the scraping and re-indexing pipeline automatically every day at 9:15 AM IST so the corpus always reflects the latest fund data published by Groww.

**Platform:** GitHub Actions (`schedule` + `workflow_dispatch`)

| Property | Value |
|---|---|
| **Workflow file** | `.github/workflows/daily_ingest.yml` |
| **Cron expression** | `15 3 * * *` (UTC) = 09:15 AM IST (UTC+5:30) |
| **Trigger types** | `schedule` (daily cron) + `workflow_dispatch` (manual on-demand run from GitHub UI) |
| **Job** | Checks out repo → sets up Python → installs deps → runs `python scripts/run_ingestion.py` |
| **Secrets** | `ANTHROPIC_API_KEY`, `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE` stored as GitHub Actions Secrets; injected as env vars at runtime |
| **Overlap protection** | `concurrency: group: ingest` with `cancel-in-progress: false` — queues rather than cancels an overlapping run |
| **Artifacts** | Scheduler logs uploaded as a GitHub Actions artifact (`logs/ingest_YYYY-MM-DD.log`) retained for 7 days |
| **Failure alerting** | GitHub notifies the repo owner by email on workflow failure |

**Workflow skeleton:**
```yaml
name: Daily Corpus Ingest

on:
  schedule:
    - cron: "15 3 * * *"   # 09:15 IST
  workflow_dispatch:         # manual trigger

concurrency:
  group: ingest
  cancel-in-progress: false

jobs:
  ingest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: python scripts/run_ingestion.py
        env:
          CHROMA_TENANT: ${{ secrets.CHROMA_TENANT }}
          CHROMA_DATABASE: ${{ secrets.CHROMA_DATABASE }}
          CHROMA_API_KEY: ${{ secrets.CHROMA_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: ingest-log
          path: logs/
          retention-days: 7
```

---

### 1. Corpus / Data Ingestion Layer

**Purpose:** Collect, parse, and normalize official source documents into a queryable corpus.

**In-scope URLs (current):**

| URL | Scheme | Category |
|---|---|---|
| `https://groww.in/mutual-funds/sbi-gold-fund-direct-growth` | SBI Gold Fund – Direct Growth | Gold / Commodity |
| `https://groww.in/mutual-funds/sbi-psu-fund-direct-growth` | SBI PSU Fund – Direct Growth | Thematic / PSU Equity |

> PDF documents (factsheets, KIM, SID) are out of scope for the current phase. Only HTML pages are ingested.

**Scraping Service — step-by-step:**

```
For each URL in corpus/urls.yaml:
  1. HTTP GET with Retry
  2. Render / parse HTML
  3. Extract target sections
  4. Clean text
  5. Tag metadata
  6. Emit (text, metadata) document
```

| Sub-component | Description |
|---|---|
| **URL Registry** | `corpus/urls.yaml` — lists the two Groww URLs with `scheme_name`, `amc_name`, and `category` fields; add new URLs here to extend the corpus |
| **HTTP Fetcher** | `requests.Session` with a realistic `User-Agent` header; retry logic (3 attempts, exponential back-off) for transient failures; raises `ScrapingError` after exhausting retries |
| **HTML Renderer** | Groww pages are partially JS-rendered; uses `requests-html` (Pyppeteer) or `Playwright` (headless Chromium) to execute JS and obtain the fully rendered DOM before parsing |
| **Section Extractor** | BeautifulSoup selectors targeting known Groww HTML structure: fund overview paragraph, key facts table (expense ratio, exit load, minimum SIP, benchmark, riskometer), and the about-the-fund section |
| **Text Cleaner** | Strips nav, cookie banner, footer, disclaimer repetitions; normalises Unicode, removes extra whitespace, collapses repeated newlines |
| **Metadata Tagger** | Attaches `source_url`, `scheme_name`, `amc_name`, `category`, `fetch_date` (ISO-8601 UTC timestamp of the fetch) to the document |
| **Change Detector** | Computes SHA-256 hash of cleaned text; compares with stored hash from the previous run; skips re-embedding if unchanged, saving embedding API cost |

**Output:** A set of cleaned text documents with structured metadata.

---

### 2. Chunking & Embedding Layer

**Purpose:** Split documents into semantically coherent chunks and encode them as vectors.

| Sub-component | Description |
|---|---|
| **Chunker** | Two-pass splitting: table-row chunks (one chunk per `KEY :: VALUE` line) + recursive character splitting (~400 token prose chunks, 50-token overlap) |
| **Embedding Model** | `BAAI/bge-small-en-v1.5` via `sentence-transformers` (local, no API key) — 384 dims, cosine similarity. Query prefix: `"Represent this sentence: "` |
| **Metadata Propagation** | Each chunk inherits parent document metadata (`source_url`, `fetch_date`, `scheme_name`) |

**Output:** List of `(vector, chunk_text, metadata)` tuples ready for indexing.

---

### 3. Vector Store

**Purpose:** Persist and index chunk embeddings for fast semantic retrieval.

| Property | Choice |
|---|---|
| **Store** | Chroma Cloud (`chromadb.CloudClient`) — managed, serverless, no local disk required |
| **Collection** | `mutual_fund_faq` |
| **Index type** | HNSW (approximate nearest neighbour) |
| **Distance metric** | Cosine |
| **Metadata filtering** | Filter by `scheme_name`, `document_type`, or `amc_name` at query time |
| **Credentials** | `CHROMA_TENANT`, `CHROMA_DATABASE`, `CHROMA_API_KEY` — set in `.env` (local) or GitHub Actions Secrets (CI) |
| **Persistence** | Fully managed by Chroma Cloud — no `data/chroma/` directory committed to the repo |

---

### 4. Query Classifier

**Purpose:** Route queries before retrieval — factual queries go to the RAG pipeline; advisory queries go to the Refusal Handler.

**Mechanism:**
- Rule-based pre-filter for keywords (`"should I"`, `"better"`, `"recommend"`, `"invest"`, `"return"`, `"performance compare"`)
- Optional LLM-based binary classifier as a fallback for ambiguous queries
- Output: `factual` | `advisory`

---

### 5. Retriever

**Purpose:** Find the top-K most relevant chunks from the vector store for a given factual query.

| Property | Value |
|---|---|
| **Retrieval method** | Dense retrieval (cosine similarity on embeddings) |
| **Top-K** | K = 4 (balances context richness vs. prompt length) |
| **Metadata filter** | Optionally scoped to a scheme or document type if detected in the query |
| **Re-ranker (optional)** | Cross-encoder re-ranking (e.g., `cross-encoder/ms-marco-MiniLM-L-6-v2`) to improve precision before passing to LLM |

---

### 6. Context Assembler

**Purpose:** Build the final prompt context from retrieved chunks and their source metadata.

- Concatenates the top-K chunk texts with a separator
- Collects the unique source URL from the highest-ranked chunk (single citation constraint)
- Records `fetch_date` for the "Last updated from sources: \<date\>" footer

---

### 7. LLM Generation Layer

**Purpose:** Generate a factual, constrained response grounded in the retrieved context.

**Model:** Claude Sonnet (Anthropic) or GPT-4o (OpenAI)

**System Prompt (Guard Rails):**
```
You are a facts-only mutual fund FAQ assistant.
- Answer ONLY using the provided context. Do not use prior knowledge.
- Responses must be 3 sentences or fewer.
- Include exactly one citation from the provided source URL.
- Append the footer: "Last updated from sources: <fetch_date>"
- Do NOT provide investment advice, recommendations, comparisons, or return projections.
- If the context does not contain the answer, say: "I don't have verified information on this. Please refer to [source_url]."
```

**Prompt Template:**
```
Context:
{retrieved_chunks}

Source: {source_url}
Fetch Date: {fetch_date}

Question: {user_query}

Answer (3 sentences max, include citation and footer):
```

---

### 8. Refusal Handler

**Purpose:** Return a polite, compliant refusal for advisory or out-of-scope queries.

**Refusal Response Template:**
```
I can only provide factual information about mutual fund schemes — 
I'm not able to offer investment advice or recommendations.
For guidance on choosing funds, please visit: https://www.amfiindia.com/investor-corner/knowledge-center
```

- Static templates for common advisory patterns
- Always includes an AMFI or SEBI educational link
- No LLM call required — reduces latency and cost

---

### 9. Response Formatter

**Purpose:** Enforce output constraints before returning to the UI.

- Truncates to 3 sentences if the LLM exceeds the limit
- Validates presence of citation link (re-appends if missing)
- Appends `Last updated from sources: <fetch_date>` footer
- Sanitizes any PII patterns (PAN, Aadhaar, account number regex) before output

---

### 10. Multi-Thread Session Manager

**Purpose:** Support multiple independent conversation threads simultaneously.

| Property | Description |
|---|---|
| **Thread ID** | UUID assigned per conversation session |
| **Conversation Store** | In-memory dict (dev) or Redis (production) keyed by thread ID |
| **History** | Stores last N turns for context continuity within a thread |
| **Isolation** | Each thread's history is fully isolated — no cross-thread data leakage |
| **Expiry** | Sessions expire after 30 minutes of inactivity |

---

### 11. User Interface Layer

**Purpose:** Minimal, compliance-aware chat UI.

| Element | Description |
|---|---|
| **Welcome message** | Brief intro to the facts-only assistant |
| **Example questions** | 3 pre-loaded factual queries (e.g., "What is the expense ratio of Mirae Asset Large Cap Fund?") |
| **Disclaimer banner** | Persistent: *"Facts-only. No investment advice."* |
| **Chat window** | Thread-aware message history with source citation rendered as a clickable link |
| **Tech** | Streamlit (MVP) or React + FastAPI (production) |

---

## Data Flow (End-to-End)

```
User types query
      │
      ▼
[Query Classifier] ──advisory──▶ [Refusal Handler] ──▶ Response
      │
   factual
      │
      ▼
[Embed query] ──▶ [Vector Store: Top-K retrieval] ──▶ [Context Assembler]
                                                              │
                                                             ▼
                                                    [LLM + System Prompt]
                                                              │
                                                             ▼
                                                   [Response Formatter]
                                                              │
                                                             ▼
                                                    Response to User
                                          (≤3 sentences, 1 citation, footer)
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| **Scheduler** | GitHub Actions (`schedule` cron `15 3 * * *` UTC + `workflow_dispatch`) |
| **HTTP fetching** | `requests` + retry via `urllib3.Retry`; `Playwright` (headless Chromium) for JS-rendered pages |
| **HTML parsing** | `BeautifulSoup4` (HTML only; no PDF in current scope) |
| **Change detection** | SHA-256 hash comparison before re-embedding |
| Embedding | `BAAI/bge-small-en-v1.5` via `sentence-transformers` (local, no API key) — 384 dims |
| Vector store | Chroma Cloud (`chromadb.CloudClient`) |
| LLM | Claude Sonnet (`claude-sonnet-4-6`) |
| Orchestration | LangChain or LlamaIndex |
| Session management | In-memory dict / Redis |
| UI | Streamlit (MVP) |
| Backend API | FastAPI |
| Config & corpus registry | YAML |

---

## Privacy & Compliance Controls

| Control | Implementation |
|---|---|
| No PII storage | No user inputs are logged or persisted beyond the session |
| PII output guard | Regex scan on LLM output before returning to user |
| Advisory refusal | Query classifier + system prompt guard (dual layer) |
| Source-only corpus | URL registry restricted to AMC, AMFI, SEBI domains |
| No third-party data | Crawler domain whitelist enforced at fetch time |

---

## Known Limitations

- Corpus is currently limited to two Groww HTML pages (SBI Gold Fund and SBI PSU Fund); queries about other schemes will not be answerable.
- PDF documents (factsheets, KIM, SID) are not ingested in this phase — detailed scheme information available only in PDFs (e.g., full SID clauses) will be missing from the knowledge base.
- Groww HTML pages are point-in-time snapshots; the corpus must be re-fetched and re-indexed to reflect fund updates (expense ratio changes, exit load revisions, etc.).
- Dense retrieval may miss exact numeric values (e.g., expense ratios) if phrased differently — a hybrid search (BM25 + dense) can mitigate this in a future phase.
- LLM hallucination risk is reduced but not eliminated; the system prompt and retrieval grounding are the primary guards.
- Performance-related queries (NAV history, returns) are intentionally out of scope and redirected to the official Groww fund page.
