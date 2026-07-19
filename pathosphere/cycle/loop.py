"""Autonomous cycle loop with resilience and state persistence.

Runs the nightly cycle repeatedly, persists state to JSON for resumability,
retries on transient failures, and logs all outcomes for monitoring.

Design:
- State file (cycle_state.json): last completed phase, timestamp, error log
- Retry logic: up to N attempts per phase (configurable, default=3)
- Idempotency: all ingest/embed/extract/cluster/graph commands are idempotent
- Monitoring: structured logging, machine-readable status file
- Graceful shutdown: Ctrl+C + caffeinate -i → process terminates cleanly
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from pathosphere.config import get_settings
from pathosphere.cycle.orchestrator import PHASE_ORDER, Phase, run_cycle


class LoopState:
    """Persistent state of the autonomous loop."""

    def __init__(self, state_file: Path):
        self.state_file = state_file

    def load(self) -> dict:
        """Load state from disk, return {last_phase, last_completion, error_log}."""
        if not self.state_file.exists():
            return {"last_phase": None, "last_completion": None, "error_log": []}
        try:
            return json.loads(self.state_file.read_text())
        except Exception as e:
            logger.warning(f"Failed to load state: {e}, starting fresh")
            return {"last_phase": None, "last_completion": None, "error_log": []}

    def save(self, last_phase: Optional[Phase], last_completion: datetime, error_log: list) -> None:
        """Persist state to disk."""
        data = {
            "last_phase": last_phase.name if last_phase else None,
            "last_completion": last_completion.isoformat() if last_completion else None,
            "error_log": error_log[-100:],  # Keep last 100 entries
        }
        self.state_file.write_text(json.dumps(data, indent=2))

    def next_phase_after(self, last_phase: Optional[Phase]) -> Optional[Phase]:
        """Return the phase to run next, given the last completed phase."""
        if last_phase is None:
            return Phase.INGEST
        try:
            idx = PHASE_ORDER.index(last_phase)
            if idx < len(PHASE_ORDER) - 1:
                return PHASE_ORDER[idx + 1]
        except ValueError:
            pass
        # Cycle complete, restart from INGEST
        return Phase.INGEST


def run_autonomous_loop(
    *,
    max_retries: int = 3,
    sleep_between_cycles: int = 3600,  # 1h default
    state_file: Optional[Path] = None,
) -> None:
    """Run autonomous cycle loop forever, resumable on failure.

    Args:
        max_retries: Retry count per phase before giving up.
        sleep_between_cycles: Seconds to sleep between cycle completions.
        state_file: Path to JSON state file (default: data/cycle_state.json).
    """
    if state_file is None:
        settings = get_settings()
        state_file = Path(settings.db_path).parent / "cycle_state.json"

    state_mgr = LoopState(state_file)
    error_log = []

    logger.info(f"Autonomous loop started. State: {state_file}")

    while True:
        try:
            # Load persistent state
            saved = state_mgr.load()
            last_phase = None
            if saved.get("last_phase"):
                try:
                    last_phase = Phase[saved["last_phase"]]
                except (KeyError, ValueError):
                    logger.warning(f"Invalid phase in state: {saved['last_phase']}")
            error_log = saved.get("error_log", [])

            # Determine where to resume
            next_phase = state_mgr.next_phase_after(last_phase)
            if next_phase is None:
                # Cycle completed; sleep before next cycle
                logger.info(
                    f"Cycle complete (last phase: {last_phase.name if last_phase else 'NONE'}). "
                    f"Sleeping {sleep_between_cycles}s before restart..."
                )
                time.sleep(sleep_between_cycles)
                next_phase = Phase.INGEST

            logger.info(f"Starting from phase {next_phase.name} (last completed: {last_phase.name if last_phase else 'NONE'})")

            # Run phases with retry
            for phase in PHASE_ORDER:
                # Skip phases before the start point
                if PHASE_ORDER.index(phase) < PHASE_ORDER.index(next_phase):
                    continue

                retry = 0
                last_error = None
                while retry < max_retries:
                    try:
                        logger.info(f"Running phase {phase.name} (attempt {retry + 1}/{max_retries})")
                        state = run_cycle(start_from=phase, dry_run=False)

                        if phase in state.errors:
                            last_error = state.errors[phase]
                            logger.error(f"{phase.name} failed: {last_error}")
                            retry += 1
                            if retry < max_retries:
                                wait = 5 * (2 ** retry)  # Exponential backoff: 10s, 20s, 40s
                                logger.info(f"Retrying in {wait}s...")
                                time.sleep(wait)
                            continue

                        # Phase succeeded
                        logger.success(f"✓ {phase.name} complete")
                        state_mgr.save(phase, datetime.utcnow(), error_log)
                        break

                    except Exception as e:
                        last_error = str(e)
                        logger.error(f"{phase.name} crashed: {e}")
                        retry += 1
                        if retry < max_retries:
                            wait = 5 * (2 ** retry)
                            logger.info(f"Retrying in {wait}s...")
                            time.sleep(wait)

                if retry >= max_retries:
                    # Phase failed after all retries
                    error_log.append({
                        "timestamp": datetime.utcnow().isoformat(),
                        "phase": phase.name,
                        "error": last_error,
                        "attempts": max_retries,
                    })
                    state_mgr.save(last_phase, datetime.utcnow(), error_log)
                    logger.error(f"Phase {phase.name} failed after {max_retries} attempts. Pausing...")
                    time.sleep(300)  # Pause 5min, then resume
                    break

        except KeyboardInterrupt:
            logger.info("Loop interrupted by user (Ctrl+C). Exiting cleanly.")
            state_mgr.save(last_phase, datetime.utcnow(), error_log)
            break
        except Exception as e:
            logger.error(f"Unexpected error in loop: {e}")
            error_log.append({
                "timestamp": datetime.utcnow().isoformat(),
                "phase": "UNKNOWN",
                "error": str(e),
            })
            time.sleep(60)
