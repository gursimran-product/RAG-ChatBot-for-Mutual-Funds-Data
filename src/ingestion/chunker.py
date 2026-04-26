"""
Two-pass chunking pipeline:
  Pass 1 — one chunk per KEY :: VALUE row from the [KEY_FACTS] section
  Pass 2 — RecursiveCharacterTextSplitter (400 tok / 50 overlap) on [OVERVIEW] prose

Chunk IDs are deterministic: {scheme_name}::{chunk_type}::{sha256(text)[:8]}
Same text → same ID → ChromaDB upsert is idempotent.
Changed text → new ID → old ID becomes stale and is cleaned up by vector_store.
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Literal

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

ChunkType = Literal["table_row", "prose"]

# Shared tokenizer — cl100k_base matches text-embedding-3-small's tokenizer
_enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    id: str
    text: str
    chunk_type: ChunkType
    metadata: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk_id(scheme_name: str, chunk_type: str, text: str) -> str:
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"{scheme_name}::{chunk_type}::{digest}"


def _token_len(text: str) -> int:
    return len(_enc.encode(text))


def _parse_sections(text: str) -> dict[str, str]:
    """Split document text into named sections by [SECTION_NAME] headers."""
    sections: dict[str, str] = {}
    current_key: str | None = None
    lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            if current_key is not None:
                sections[current_key] = "\n".join(lines).strip()
            current_key = line[1:-1].lower()
            lines = []
        else:
            lines.append(line)

    if current_key is not None:
        sections[current_key] = "\n".join(lines).strip()

    return sections


# ---------------------------------------------------------------------------
# Pass 1 — Table row chunking
# ---------------------------------------------------------------------------

def _table_row_chunks(key_facts_text: str, metadata: dict) -> list[Chunk]:
    """
    Each 'LABEL :: VALUE' line becomes its own chunk.
    Guarantees a query like "expense ratio?" retrieves exactly that row.
    """
    chunks: list[Chunk] = []

    for line in key_facts_text.splitlines():
        line = line.strip()
        if "::" not in line:
            continue

        label, _, _ = line.partition("::")
        field_key = label.strip().lower().replace(" ", "_").replace("/", "_")

        chunks.append(
            Chunk(
                id=_chunk_id(metadata["scheme_name"], "table_row", line),
                text=line,
                chunk_type="table_row",
                metadata={
                    **metadata,
                    "chunk_type": "table_row",
                    "field": field_key,
                },
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Pass 2 — Prose chunking
# ---------------------------------------------------------------------------

def _prose_chunks(prose_text: str, metadata: dict) -> list[Chunk]:
    """
    Split prose with RecursiveCharacterTextSplitter.
    chunk_size=400 tokens, overlap=50 tokens, measured via tiktoken.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=50,
        separators=["\n\n", "\n", ". ", " "],
        length_function=_token_len,
    )

    chunks: list[Chunk] = []
    for split in splitter.split_text(prose_text):
        split = split.strip()
        if not split:
            continue
        chunks.append(
            Chunk(
                id=_chunk_id(metadata["scheme_name"], "prose", split),
                text=split,
                chunk_type="prose",
                metadata={**metadata, "chunk_type": "prose"},
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_document(document: dict) -> list[Chunk]:
    """
    Chunk a single scraped Document using the two-pass strategy.
    Returns all chunks for that document with inherited metadata.
    """
    metadata = document["metadata"]
    sections = _parse_sections(document["text"])
    all_chunks: list[Chunk] = []

    # Pass 1 — structured table rows (NAV, SIP, AUM, expense ratio, rating, etc.)
    key_facts = sections.get("key_facts", "")
    if key_facts:
        rows = _table_row_chunks(key_facts, metadata)
        all_chunks.extend(rows)
        logger.debug("%s: %d table_row chunk(s)", metadata["scheme_name"], len(rows))

    # Pass 2 — fund overview / investment objective prose
    overview = sections.get("overview", "")
    if overview:
        prose = _prose_chunks(overview, metadata)
        all_chunks.extend(prose)
        logger.debug("%s: %d prose chunk(s)", metadata["scheme_name"], len(prose))

    n_table = sum(1 for c in all_chunks if c.chunk_type == "table_row")
    n_prose = sum(1 for c in all_chunks if c.chunk_type == "prose")
    logger.info(
        "Chunked %s → %d total (%d table_row + %d prose)",
        metadata["scheme_name"], len(all_chunks), n_table, n_prose,
    )

    return all_chunks


def chunk_documents(documents: list[dict]) -> list[Chunk]:
    """Chunk all documents and return a flat list of all chunks."""
    all_chunks: list[Chunk] = []
    for doc in documents:
        all_chunks.extend(chunk_document(doc))
    return all_chunks
