"""
Context Assembler — Component 6.

Turns retrieved chunks into the structured context block passed to the LLM:
  - Concatenates chunk texts (separated by "---")
  - Picks the single citation URL from the top-ranked chunk
  - Extracts fetch_date for the "Last updated" footer
"""

import logging

logger = logging.getLogger(__name__)


def assemble(hits: list[dict]) -> dict:
    """
    Args:
        hits: ordered list of retrieval results from retriever.retrieve()
              each has keys: text, metadata, distance

    Returns a dict:
        {
          "context_text": str,   # chunk texts joined for the LLM prompt
          "source_url":   str,   # citation from top-ranked chunk
          "fetch_date":   str,   # ISO-8601 fetch_date from top-ranked chunk
          "scheme_name":  str,   # for display / logging
        }
    """
    if not hits:
        return {
            "context_text": "",
            "source_url": "",
            "fetch_date": "",
            "scheme_name": "",
        }

    top = hits[0]["metadata"]

    context_text = "\n---\n".join(h["text"] for h in hits)

    assembled = {
        "context_text": context_text,
        "source_url":   top.get("source_url", ""),
        "fetch_date":   top.get("fetch_date", ""),
        "scheme_name":  top.get("scheme_name", ""),
    }

    logger.debug(
        "Assembled context: %d chunk(s), source=%s",
        len(hits), assembled["source_url"],
    )
    return assembled
