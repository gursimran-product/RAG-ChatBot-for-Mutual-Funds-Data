# Deployment Plan — Mutual Fund FAQ Assistant

## Platform Summary

| Component | Platform | Trigger |
|---|---|---|
| Daily ingestion scheduler | GitHub Actions | Cron `15 3 * * *` (09:15 IST) |
| FastAPI backend | Render (Web Service) | Auto-deploy on every push to `main` |
| Streamlit frontend | Vercel | Auto-deploy on every push to `main` |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  GitHub Actions (Scheduler)              │
│  Cron: 09:15 IST daily                                   │
│                                                          │
│  Phase 1 — Playwright scrape Groww pages                 │
│  Phase 2 — Extract fund data → commit fund_data.json     │
│  Phase 3 — SHA-256 change detection                      │
│  Phase 4 — Two-pass chunking                             │
│  Phase 5 — bge-small-en-v1.5 embed → Chroma Cloud       │
└──────────────────┬──────────────────────────────────────┘
                   │ git push (fund_data.json + hashes.json)
                   ▼
            GitHub Repository ──────────────────────┐
                   │                                 │
          auto-deploy on push                auto-deploy on push
                   │                                 │
                   ▼                                 ▼
     ┌─────────────────────┐           ┌─────────────────────┐
     │  Render             │           │  Vercel             │
     │  FastAPI backend    │◄──────────│  Streamlit frontend │
     │  app/api.py         │  API_BASE │  app/ui.py          │
     │  uvicorn :8000      │           │  streamlit run      │
     └──────────┬──────────┘           └─────────────────────┘
                │
                ▼
     ┌─────────────────────┐
     │  Chroma Cloud       │
     │  mutual_fund_faq    │
     │  collection (384d)  │
     └─────────────────────┘
```

**Key data flow**: GitHub Actions commits `data/fund_data.json` back to the repo after every successful ingest. This push triggers a Render auto-deploy, so the backend always serves fresh NAV/SIP/AUM data without a manual redeploy.

---

## 1. GitHub Actions — Scheduler

The workflow is already at `.github/workflows/daily_ingest.yml`.
Only the three repository secrets need to be configured.

### Required Repository Secrets

Go to **GitHub → Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret Name | Where to get it |
|---|---|
| `CHROMA_TENANT` | Chroma Cloud dashboard → Settings |
| `CHROMA_DATABASE` | Chroma Cloud dashboard → Database name |
| `CHROMA_API_KEY` | Chroma Cloud dashboard → API Keys |

> `GROQ_API_KEY` is **not** needed by the scheduler — it is only used by the backend at query time.

### Verify the workflow runs

After adding secrets, go to **Actions → Daily Corpus Ingest → Run workflow** to trigger a manual test run. A successful run will:
1. Scrape 2 Groww fund pages
2. Extract and commit `data/fund_data.json` and `data/hashes.json`
3. Embed changed chunks into Chroma Cloud
4. Upload `logs/` as a build artifact (7-day retention)

---

## 2. Render — FastAPI Backend

### Service type
**Web Service** (not a static site). Render runs `uvicorn` as a long-lived process.

### Deployment steps

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect the GitHub repository
3. Set the following fields:

| Field | Value |
|---|---|
| **Name** | `mutual-fund-faq-api` (or any name) |
| **Region** | Singapore (closest to India) |
| **Branch** | `main` |
| **Root Directory** | *(leave blank — repo root)* |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app.api:app --host 0.0.0.0 --port $PORT` |

4. Under **Auto-Deploy**, set to **Yes** — this ensures every `git push` (including the daily ingest commit) automatically redeploys the service with the latest `fund_data.json`.

### Environment Variables

Add these in **Render → Environment → Add Environment Variable**:

| Key | Value |
|---|---|
| `GROQ_API_KEY` | Your Groq API key |
| `CHROMA_TENANT` | Your Chroma Cloud tenant ID |
| `CHROMA_DATABASE` | Your Chroma Cloud database name |
| `CHROMA_API_KEY` | Your Chroma Cloud API key |

> No `HF_HOME` is needed here — the backend never loads the embedding model. Embeddings are computed only during ingestion (GitHub Actions).

### Health check

Once deployed, confirm the backend is live:

```
GET https://<your-render-url>.onrender.com/health
```

Expected response:
```json
{ "status": "ok", "active_sessions": 0 }
```

### CORS

`app/api.py` already sets `allow_origins=["*"]`. Once the Vercel frontend URL is stable, tighten this to:
```python
allow_origins=["https://<your-vercel-app>.vercel.app"]
```

---

## 3. Vercel — Streamlit Frontend

> **Note on Vercel and Streamlit**: Vercel is a serverless platform optimised for Node.js and static frontends. Streamlit runs as a persistent WebSocket server and is not natively compatible with Vercel's serverless model. The steps below use Vercel's Python runtime to wrap the Streamlit process, but there are connection timeout constraints (responses must complete within 30 s on the free plan).
>
> **Recommended alternative**: [Streamlit Community Cloud](https://streamlit.io/cloud) is purpose-built for Streamlit apps, free, and deploys directly from GitHub in one click with no configuration. If the Vercel constraint becomes a problem during testing, Streamlit Community Cloud is a drop-in replacement — the same `app/ui.py` file works unchanged.

### Option A — Vercel (as specified)

#### Create `vercel.json` in the repo root

```json
{
  "version": 2,
  "builds": [
    {
      "src": "app/ui.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    {
      "src": "/(.*)",
      "dest": "app/ui.py"
    }
  ]
}
```

#### Deployment steps

1. Go to [vercel.com](https://vercel.com) → **New Project → Import Git Repository**
2. Select the GitHub repo
3. Set the following:

| Field | Value |
|---|---|
| **Framework Preset** | Other |
| **Root Directory** | *(leave blank — repo root)* |
| **Build Command** | `pip install -r requirements.txt` |
| **Output Directory** | *(leave blank)* |

4. Under **Environment Variables**, add:

| Key | Value |
|---|---|
| `API_BASE` | `https://<your-render-url>.onrender.com` |

> `app/ui.py` already reads `API_BASE` from the environment (`os.environ.get("API_BASE", "http://localhost:8000")`), so only this one variable is needed on the frontend.

5. Click **Deploy**.

#### Known limitation

Vercel serverless functions time out after 10–30 seconds (plan-dependent). If the Render cold-start causes the first `/session` call to exceed this, the UI will show a connection error. Subsequent requests (Render warmed up) will succeed. Use the [Render paid plan](https://render.com/pricing) to avoid cold starts if this is a concern.

---

### Option B — Streamlit Community Cloud (recommended for Streamlit)

1. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
2. Connect the GitHub repo
3. Set:

| Field | Value |
|---|---|
| **Repository** | `<your-github-username>/<repo-name>` |
| **Branch** | `main` |
| **Main file path** | `app/ui.py` |

4. Under **Advanced settings → Secrets**, add:

```toml
API_BASE = "https://<your-render-url>.onrender.com"
```

5. Click **Deploy**. The app will be live at `https://<your-app>.streamlit.app`.

---

## 4. Deployment Sequence

Deploy in this order to avoid broken dependencies:

```
Step 1 — Chroma Cloud          Already set up (collection exists)
Step 2 — GitHub Secrets        Add CHROMA_* secrets to the repo
Step 3 — Render backend        Deploy, verify /health returns 200
Step 4 — Frontend (Vercel/SC)  Set API_BASE to the Render URL, deploy
Step 5 — Test end-to-end       Open frontend → ask a question → get answer
Step 6 — Verify scheduler      Trigger workflow_dispatch → confirm ingest completes
```

---

## 5. Environment Variables — Full Reference

| Variable | Scheduler (GHA) | Backend (Render) | Frontend (Vercel/SC) |
|---|:---:|:---:|:---:|
| `GROQ_API_KEY` | — | Yes | — |
| `CHROMA_TENANT` | Yes | Yes | — |
| `CHROMA_DATABASE` | Yes | Yes | — |
| `CHROMA_API_KEY` | Yes | Yes | — |
| `HF_HOME` | Yes (`.hf_cache`) | — | — |
| `API_BASE` | — | — | Yes |

---

## 6. Post-Deployment Checks

| Check | How |
|---|---|
| Scheduler fires correctly | GitHub Actions → Daily Corpus Ingest → check green run at ~09:15 IST |
| `fund_data.json` is updated | Inspect latest commit on `main` — should be `chore: daily corpus ingest YYYY-MM-DD` |
| Render auto-redeploys | Check Render dashboard → Deploys tab — new deploy triggered by the ingest commit |
| Backend `/health` responds | `curl https://<render-url>/health` |
| Backend `/funds` returns data | `curl https://<render-url>/funds` — should show NAV, SIP, AUM for both funds |
| Frontend loads | Open the Vercel/Streamlit URL — disclaimer banner and example questions visible |
| Chat works end-to-end | Ask "What is the expense ratio of SBI Gold Fund?" — expect `0.25%` in the answer |
| Advisory refusal works | Ask "Should I invest in SBI Gold Fund?" — expect a refusal, not a recommendation |
