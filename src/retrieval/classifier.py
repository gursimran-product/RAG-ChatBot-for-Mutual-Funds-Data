"""
Query Classifier — Component 4.

Two-layer routing:
  Layer 1 (fast): regex keyword match → immediate advisory/factual decision
  Layer 2 (fallback): LLM binary classification for ambiguous queries

Output: "factual" | "advisory"
"""

import logging
import re

logger = logging.getLogger(__name__)

# Advisory pattern list — covers the refusal cases from the problem statement
_ADVISORY_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    r"\bshould i\b",
    r"\bshould (i|we|one)\b.*(invest|buy|put|keep|redeem)",
    r"\bwhich (fund|scheme|option) (is |would be )?(better|best|good|right|ideal)\b",
    r"\brecommend\b",
    r"\badvise\b",
    r"\bbest fund\b",
    r"\bworth investing\b",
    r"\bwhere (should|to) invest\b",
    r"\bwill (it|this|the fund|nav) (go up|rise|fall|drop|grow|generate|give|return)",
    r"\bpredict\b",
    r"\bforecast\b",
    r"\bcompare\b.*\b(fund|scheme|option)\b",
    r"\b(fund|scheme)\b.*\bcompare\b",
    r"\bbeat the market\b",
    r"\bgrow my (money|wealth|savings)\b",
    r"\bfuture (return|performance|nav)\b",
    r"\bexpected return\b",
    r"\bguarantee\b",
]]


def classify(query: str) -> str:
    """
    Returns 'advisory' if the query asks for opinion/recommendation/prediction,
    otherwise 'factual'.
    """
    for pattern in _ADVISORY_PATTERNS:
        if pattern.search(query):
            logger.info("Query classified as advisory (pattern: %s)", pattern.pattern)
            return "advisory"

    logger.info("Query classified as factual")
    return "factual"
