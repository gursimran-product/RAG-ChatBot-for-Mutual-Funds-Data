"""
Local scheduler — mirrors the GitHub Actions daily ingest job.
Schedule: daily at 09:15 IST, matching .github/workflows/daily_ingest.yml cron "15 3 * * *".

Usage:
    python scripts/scheduler.py           # daemon: fires daily at 09:15 IST
    python scripts/scheduler.py --now     # fire immediately, then enter the daily daemon loop
    python scripts/scheduler.py --once    # fire immediately and exit (for phase verification)
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Python path bootstrap
# On this machine C:\Python314\python.exe has its stdlib on sys.path but
# site-packages is missing (the install split Lib from the executable).
# Detect the site-packages directory from the Lib path already on sys.path
# and inject it as PYTHONPATH so the subprocess can import installed packages.
# ---------------------------------------------------------------------------

def _detect_site_packages() -> str | None:
    """Return the first site-packages dir found adjacent to a Lib entry in sys.path."""
    for p in sys.path:
        candidate = Path(p) / "site-packages"
        if candidate.is_dir() and (candidate / "dotenv").is_dir():
            return str(candidate)
    return None

SCHEDULER_LOG = LOG_DIR / "scheduler.log"

IST = timezone(timedelta(hours=5, minutes=30))
DAILY_HOUR_IST = 9
DAILY_MINUTE_IST = 15

# ---------------------------------------------------------------------------
# Logging — dedicated scheduler log, separate from the per-day ingest log
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(SCHEDULER_LOG, mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_run_time() -> datetime:
    """Return the next 09:15 IST datetime strictly after now."""
    now = datetime.now(IST)
    candidate = now.replace(
        hour=DAILY_HOUR_IST, minute=DAILY_MINUTE_IST, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _run_pipeline() -> bool:
    """
    Invoke run_ingestion.py in a subprocess.
    The ingest script handles its own per-day log file (logs/ingest_YYYY-MM-DD.log).
    We record the scheduler-level envelope (trigger time, duration, exit code) here.
    """
    trigger_ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    logger.info(">>> Trigger at %s — spawning ingestion pipeline", trigger_ts)
    logger.info("    Phases: 1-Scrape  2-FundData  3-ChangeDetect  4-Chunk  5-Embed+Upsert")

    env = os.environ.copy()
    site_pkgs = _detect_site_packages()
    if site_pkgs:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{site_pkgs}{os.pathsep}{existing}" if existing else site_pkgs
        logger.info("    PYTHONPATH → %s", site_pkgs)

    t0 = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_ingestion.py")],
        cwd=str(ROOT),
        env=env,
    )
    elapsed = time.monotonic() - t0

    if result.returncode == 0:
        logger.info("<<< Pipeline SUCCESS — elapsed %.1fs (exit 0)", elapsed)
        return True
    else:
        logger.error(
            "<<< Pipeline FAILED — elapsed %.1fs (exit %d)",
            elapsed,
            result.returncode,
        )
        return False


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

def _daemon_loop() -> None:
    """Sleep-poll loop: fires the pipeline once per day at 09:15 IST."""
    next_run = _next_run_time()
    logger.info(
        "Daemon loop active — next fire at %s",
        next_run.strftime("%Y-%m-%d %H:%M:%S IST"),
    )

    try:
        while True:
            if datetime.now(IST) >= next_run:
                _run_pipeline()
                next_run = _next_run_time()
                logger.info(
                    "Next run scheduled at %s",
                    next_run.strftime("%Y-%m-%d %H:%M:%S IST"),
                )
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user (Ctrl+C).")
        logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local ingest scheduler — mirrors GitHub Actions daily_ingest.yml"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--now",
        action="store_true",
        help="Run one pipeline immediately, then enter the daily daemon loop",
    )
    group.add_argument(
        "--once",
        action="store_true",
        help="Run one pipeline immediately and exit (phase verification mode)",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Scheduler starting up")
    logger.info("  Schedule : daily %02d:%02d IST (mirrors GHA cron '15 3 * * *')", DAILY_HOUR_IST, DAILY_MINUTE_IST)
    logger.info("  Ingest log: logs/ingest_<date>.log  (written by run_ingestion.py)")
    logger.info("  Sched log : %s", SCHEDULER_LOG)
    logger.info("=" * 60)

    if args.once:
        logger.info("Mode: --once  (single immediate run, then exit)")
        success = _run_pipeline()
        logger.info("Scheduler exiting — mode --once complete")
        logger.info("=" * 60)
        sys.exit(0 if success else 1)

    if args.now:
        logger.info("Mode: --now  (immediate run, then daily daemon)")
        _run_pipeline()

    _daemon_loop()


if __name__ == "__main__":
    main()
