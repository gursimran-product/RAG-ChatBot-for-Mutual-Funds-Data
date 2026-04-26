"""
LLM Generation Layer — Component 7.

Uses Groq API (llama-3.3-70b-versatile) for fast, low-latency inference.
System prompt enforces: facts-only, ≤3 sentences, 1 citation, last-updated footer.
"""

import logging
import os

from groq import Groq

logger = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"

_SYSTEM_PROMPT = """\
You are a facts-only mutual fund FAQ assistant for SBI Mutual Fund schemes.

Rules you must follow without exception:
1. Answer ONLY using the information in the provided Context. Do not use prior knowledge.
2. Your response must be 3 sentences or fewer.
3. End your response with exactly one citation on its own line in this format:
   Source: <source_url>
4. After the citation, add this footer on its own line:
   Last updated from sources: <fetch_date>
5. Do NOT provide investment advice, return projections, performance comparisons, or recommendations.
6. If the Context does not contain the answer, respond with:
   "I don't have verified information on this topic. Please refer to the official source: <source_url>
   Last updated from sources: <fetch_date>"
"""

_PROMPT_TEMPLATE = """\
Context:
{context_text}

Source: {source_url}
Fetch Date: {fetch_date}

Question: {user_query}

Answer (3 sentences max, include Source citation and Last updated footer):"""


_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY environment variable is not set")
        _client = Groq(api_key=api_key)
    return _client


def generate(context: dict, user_query: str) -> str:
    """
    Generate a grounded factual answer.

    Args:
        context: assembled context dict from context_assembler.assemble()
        user_query: the original user question

    Returns:
        LLM response string (already includes citation + footer).
    """
    prompt = _PROMPT_TEMPLATE.format(
        context_text=context["context_text"],
        source_url=context["source_url"],
        fetch_date=context["fetch_date"],
        user_query=user_query,
    )

    logger.info("Calling LLM (%s) for query: %.60s...", MODEL, user_query)

    completion = _get_client().chat.completions.create(
        model=MODEL,
        max_tokens=256,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
    )

    response = completion.choices[0].message.content
    logger.info("LLM responded (%d chars)", len(response))
    return response
