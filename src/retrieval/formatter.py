"""
Response Formatter — Component 9.

Post-processes LLM output before returning to the user:
  1. Truncates to 3 sentences if the LLM exceeded the limit
  2. Ensures citation line is present (re-appends if missing)
  3. Ensures "Last updated" footer is present (re-appends if missing)
  4. Strips any PII patterns (PAN, Aadhaar, account numbers, phone, email)
"""

import re

# --- PII patterns to strip from LLM output ---
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),          "[PAN REDACTED]"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),           "[AADHAAR REDACTED]"),
    (re.compile(r"\b\d{9,18}\b"),                         "[ACCOUNT REDACTED]"),
    (re.compile(r"\b[6-9]\d{9}\b"),                       "[PHONE REDACTED]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b"),   "[EMAIL REDACTED]"),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _truncate_to_sentences(text: str, max_sentences: int = 3) -> str:
    """Split on sentence boundaries and keep at most max_sentences."""
    # Don't truncate if the text contains a Source: or Last updated: line —
    # those are structured lines, not prose sentences.
    prose_lines = [
        line for line in text.splitlines()
        if not line.startswith("Source:") and not line.startswith("Last updated")
    ]
    prose = " ".join(prose_lines).strip()
    sentences = _SENTENCE_SPLIT.split(prose)
    truncated = " ".join(sentences[:max_sentences]).strip()
    return truncated


def _strip_pii(text: str) -> str:
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def format_response(raw: str, source_url: str, fetch_date: str) -> str:
    """
    Apply all formatting rules to the raw LLM output.
    Returns the final response string shown to the user.
    """
    # 1. PII scan first (before any other processing)
    text = _strip_pii(raw)

    # 2. Split into prose and structured footer lines
    lines = text.splitlines()
    prose_lines: list[str] = []
    source_line: str | None = None
    updated_line: str | None = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Source:"):
            source_line = stripped
        elif stripped.startswith("Last updated"):
            updated_line = stripped
        else:
            prose_lines.append(line)

    # 3. Truncate prose to 3 sentences
    prose_text = "\n".join(prose_lines).strip()
    prose_text = _truncate_to_sentences(prose_text, max_sentences=3)

    # 4. Ensure citation is present
    if not source_line:
        source_line = f"Source: {source_url}" if source_url else "Source: (see official fund page)"

    # 5. Ensure footer is present
    if not updated_line:
        updated_line = f"Last updated from sources: {fetch_date}" if fetch_date else ""

    # 6. Reassemble
    parts = [prose_text, source_line]
    if updated_line:
        parts.append(updated_line)

    return "\n".join(p for p in parts if p)
