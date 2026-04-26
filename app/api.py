"""
FastAPI backend — serves the query pipeline over HTTP.

Endpoints:
  POST /session          → create a new thread_id
  POST /chat             → send a query, get an answer
  GET  /session/{id}     → get session history
  GET  /funds            → list fund records from data/fund_data.json
  GET  /health           → liveness check
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.ingestion.fund_data_store import load_fund_data
from src.retrieval.pipeline import answer
from src.retrieval.session_manager import sessions

app = FastAPI(
    title="Mutual Fund FAQ Assistant",
    description="Facts-only Q&A for SBI Mutual Fund schemes. No investment advice.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    thread_id: str
    query: str


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    query_type: str   # "factual" | "advisory"
    source_url: str
    fetch_date: str


class SessionResponse(BaseModel):
    thread_id: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "active_sessions": sessions.active_count}


@app.post("/session", response_model=SessionResponse)
def create_session():
    """Create a new isolated conversation thread."""
    thread_id = sessions.create_session()
    return {"thread_id": thread_id}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Send a query and receive a facts-only answer."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    result = answer(query=req.query.strip(), thread_id=req.thread_id)

    return ChatResponse(
        thread_id=req.thread_id,
        answer=result["answer"],
        query_type=result["query_type"],
        source_url=result["source_url"],
        fetch_date=result["fetch_date"],
    )


@app.get("/session/{thread_id}")
def get_session_history(thread_id: str):
    """Return the conversation history for a thread."""
    history = sessions.get_history(thread_id)
    return {"thread_id": thread_id, "history": history}


@app.get("/funds")
def list_funds():
    """Return the latest structured fund data (NAV, SIP, AUM, ER, Rating)."""
    return load_fund_data()
