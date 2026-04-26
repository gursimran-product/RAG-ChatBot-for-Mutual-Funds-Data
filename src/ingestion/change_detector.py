import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

HASH_STORE_PATH = Path(__file__).parent.parent.parent / "data" / "hashes.json"


def _load_stored_hashes() -> dict[str, str]:
    if not HASH_STORE_PATH.exists():
        return {}
    with open(HASH_STORE_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_hashes(hashes: dict[str, str]) -> None:
    HASH_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HASH_STORE_PATH, "w", encoding="utf-8") as fh:
        json.dump(hashes, fh, indent=2)
    logger.info("Saved content hashes to %s", HASH_STORE_PATH)


def filter_changed(documents: list) -> tuple[list, dict[str, str]]:
    """
    Compare each document's content_hash against the stored hash from the
    previous run. Returns:
      - changed_docs  : only documents whose content has changed (need re-embed)
      - updated_hashes: full hash map to persist after a successful pipeline run
    """
    stored = _load_stored_hashes()
    changed: list = []
    updated_hashes: dict[str, str] = dict(stored)

    for doc in documents:
        scheme = doc["metadata"]["scheme_name"]
        current_hash = doc["metadata"]["content_hash"]

        if stored.get(scheme) == current_hash:
            logger.info("No change: %s — skipping re-embed", scheme)
        else:
            logger.info("Changed: %s — queued for re-embed", scheme)
            changed.append(doc)
            updated_hashes[scheme] = current_hash

    return changed, updated_hashes
