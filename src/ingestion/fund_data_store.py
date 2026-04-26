"""
Structured store for the 5 key fund data fields:
  NAV, Minimum SIP, Fund Size, Expense Ratio, Rating

Written to data/fund_data.json after every successful scrape.
Provides O(1) lookup for the retrieval layer — no vector search needed
for direct field queries.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

FUND_DATA_PATH = Path(__file__).parent.parent.parent / "data" / "fund_data.json"


class FundRecord(TypedDict):
    scheme_name: str
    display_name: str
    source_url: str
    nav: str               # e.g. "₹24.56"
    minimum_sip: str       # e.g. "₹500"
    fund_size: str         # e.g. "₹1,234 Cr"  (AUM)
    expense_ratio: str     # e.g. "0.18%"
    rating: str            # e.g. "4" (stars) or "Not Rated"
    last_updated: str      # ISO-8601 UTC


# ---------------------------------------------------------------------------
# Field extraction from raw section text
# ---------------------------------------------------------------------------

# Maps each canonical field to the set of label variations Groww may use.
_FIELD_ALIASES: dict[str, list[str]] = {
    "nav":            ["nav"],
    "minimum_sip":    ["min. for sip", "min for sip", "min sip", "minimum sip", "minimum sip amount", "sip amount"],
    "fund_size":      ["fund size (aum)", "fund size", "aum", "assets under management", "corpus"],
    "expense_ratio":  ["expense ratio", "total expense ratio", "ter"],
    "rating":         ["rating", "fund rating", "value research rating", "morningstar rating", "crisil rating"],
}


def extract_fund_fields(key_facts_text: str) -> dict[str, str]:
    """
    Parse the KEY_FACTS block (lines of the form 'Label :: Value') and
    return a dict with exactly the 5 canonical field keys.
    Missing fields are recorded as 'N/A'.
    """
    result: dict[str, str] = {field: "N/A" for field in _FIELD_ALIASES}

    for line in key_facts_text.splitlines():
        if "::" not in line:
            continue
        raw_label, _, raw_value = line.partition("::")
        label = raw_label.strip().lower()
        value = raw_value.strip()

        for canonical, aliases in _FIELD_ALIASES.items():
            if any(alias in label for alias in aliases):
                # Only overwrite if we haven't already found this field
                # (first match wins — avoids clobbering with a less specific alias)
                if result[canonical] == "N/A":
                    result[canonical] = value
                break

    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_fund_data() -> dict[str, FundRecord]:
    """Load all stored fund records, keyed by scheme_name."""
    if not FUND_DATA_PATH.exists():
        return {}
    with open(FUND_DATA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_fund_record(record: FundRecord) -> None:
    """Upsert a single fund record into fund_data.json."""
    FUND_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_records = load_fund_data()
    all_records[record["scheme_name"]] = record

    with open(FUND_DATA_PATH, "w", encoding="utf-8") as fh:
        json.dump(all_records, fh, indent=2, ensure_ascii=False)

    logger.info(
        "Saved fund record for %s → NAV=%s | SIP=%s | AUM=%s | ER=%s | Rating=%s",
        record["scheme_name"],
        record["nav"],
        record["minimum_sip"],
        record["fund_size"],
        record["expense_ratio"],
        record["rating"],
    )


def build_and_save_fund_record(document: dict) -> FundRecord:
    """
    Extract the 5 key fields from a scraped Document and persist them.
    Call this once per document after scraping, before chunking.
    """
    meta = document["metadata"]
    text = document["text"]

    # Isolate the KEY_FACTS block
    key_facts_block = ""
    for section in text.split("\n\n"):
        if section.startswith("[KEY_FACTS]"):
            key_facts_block = section.replace("[KEY_FACTS]", "").strip()
            break

    fields = extract_fund_fields(key_facts_block)

    record: FundRecord = {
        "scheme_name":    meta["scheme_name"],
        "display_name":   meta["display_name"],
        "source_url":     meta["source_url"],
        "nav":            fields["nav"],
        "minimum_sip":    fields["minimum_sip"],
        "fund_size":      fields["fund_size"],
        "expense_ratio":  fields["expense_ratio"],
        "rating":         fields["rating"],
        "last_updated":   meta["fetch_date"],
    }

    save_fund_record(record)
    return record
