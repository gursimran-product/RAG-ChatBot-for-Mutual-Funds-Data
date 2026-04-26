"""
Query Pipeline — end-to-end orchestration of the online inference path.

Flow:
  Query
    │
    ▼
  [Classifier]  ──advisory──▶  [Refusal Handler]  ──▶  Response
    │
  factual
    │
    ▼
  [Retriever]  ──▶  [Context Assembler]  ──▶  [LLM]  ──▶  [Formatter]  ──▶  Response
"""

import logging

from src.retrieval.classifier import classify
from src.retrieval.context_assembler import assemble
from src.retrieval.formatter import format_response
from src.retrieval.llm import generate
from src.retrieval.refusal_handler import get_refusal
from src.retrieval.retriever import retrieve
from src.retrieval.session_manager import sessions

logger = logging.getLogger(__name__)


def answer(query: str, thread_id: str) -> dict:
    """
    Process a user query end-to-end.

    Args:
        query:     The user's question string.
        thread_id: Session UUID (from SessionManager).

    Returns a dict:
        {
          "answer":      str,   # final response shown to the user
          "query_type":  str,   # "factual" | "advisory"
          "source_url":  str,   # citation URL (empty for advisory)
          "fetch_date":  str,   # last updated date (empty for advisory)
        }
    """
    logger.info("[%s] Query: %.80s", thread_id, query)

    # Record user turn in session history
    sessions.add_turn(thread_id, "user", query)

    # --- Component 4: Query Classifier ---
    query_type = classify(query)

    if query_type == "advisory":
        # --- Component 8: Refusal Handler (no LLM call) ---
        response_text = get_refusal()
        sessions.add_turn(thread_id, "assistant", response_text)
        return {
            "answer":     response_text,
            "query_type": "advisory",
            "source_url": "",
            "fetch_date": "",
        }

    # --- Component 5: Retriever ---
    hits = retrieve(query)

    if not hits:
        fallback = (
            "I don't have verified information on this topic in my current knowledge base. "
            "Please refer to the official fund pages at https://groww.in/mutual-funds."
        )
        sessions.add_turn(thread_id, "assistant", fallback)
        return {
            "answer":     fallback,
            "query_type": "factual",
            "source_url": "",
            "fetch_date": "",
        }

    # --- Component 6: Context Assembler ---
    context = assemble(hits)

    # --- Component 7: LLM Generation ---
    raw_response = generate(context, query)

    # --- Component 9: Response Formatter ---
    final_response = format_response(
        raw=raw_response,
        source_url=context["source_url"],
        fetch_date=context["fetch_date"],
    )

    sessions.add_turn(thread_id, "assistant", final_response)

    logger.info("[%s] Response: %.80s...", thread_id, final_response)
    return {
        "answer":     final_response,
        "query_type": "factual",
        "source_url": context["source_url"],
        "fetch_date": context["fetch_date"],
    }
