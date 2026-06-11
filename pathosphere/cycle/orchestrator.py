"""
Nightly cycle orchestrator.

Phases (sequential, resumable):
  1. ingest   — download documents from sources
  2. embed    — compute embeddings + vector dedup
  3. extract  — NER, geocoding, entity linking
  4. cluster  — group articles into events
  5. brief    — generate morning brief with Qwen

Each phase is atomic: if interrupted, resumes from the last completed phase.
"""

from dataclasses import dataclass, field
from enum import Enum, auto

from loguru import logger


class Phase(Enum):
    INGEST = auto()
    EMBED = auto()
    EXTRACT = auto()
    CLUSTER = auto()
    BRIEF = auto()


PHASE_ORDER = [
    Phase.INGEST,
    Phase.EMBED,
    Phase.EXTRACT,
    Phase.CLUSTER,
    Phase.BRIEF,
]


@dataclass
class CycleState:
    completed: set[Phase] = field(default_factory=set)
    errors: dict[Phase, str] = field(default_factory=dict)


def run_cycle(
    *,
    start_from: Phase | None = None,
    dry_run: bool = False,
) -> CycleState:
    """Run the nightly cycle sequentially and resumably."""
    state = CycleState()
    skip = start_from is not None

    for phase in PHASE_ORDER:
        if skip:
            if phase == start_from:
                skip = False
            else:
                logger.info(f"Skipping phase {phase.name} (resuming from {start_from.name})")
                continue

        logger.info(f"→ Phase {phase.name}")

        if dry_run:
            logger.info(f"  [dry-run] {phase.name} simulated")
            state.completed.add(phase)
            continue

        try:
            _run_phase(phase)
            state.completed.add(phase)
            logger.success(f"✓ {phase.name} complete")
        except Exception as exc:
            state.errors[phase] = str(exc)
            logger.error(f"✗ {phase.name} failed: {exc}")
            break

    return state


def _run_phase(phase: Phase) -> None:
    match phase:
        case Phase.INGEST:
            _phase_ingest()
        case Phase.EMBED:
            _phase_embed()
        case Phase.EXTRACT:
            _phase_extract()
        case Phase.CLUSTER:
            _phase_cluster()
        case Phase.BRIEF:
            _phase_brief()


def _phase_ingest() -> None:
    from pathosphere.config import get_settings
    from pathosphere.db.schema import get_connection
    from pathosphere.ingest.gdelt import QUAD_CONFLICT, ingest_gdelt

    settings = get_settings()
    conn = get_connection(settings.db_path)
    result = ingest_gdelt(
        conn,
        n_days=1,
        quad_classes=QUAD_CONFLICT,
        min_mentions=10,
        skip_existing=True,
    )
    conn.close()
    logger.info(
        f"INGEST: {result.events_inserted} events, {result.docs_inserted} docs"
    )


def _phase_embed() -> None:
    raise NotImplementedError("Embedding not yet implemented (Phase 2)")


def _phase_extract() -> None:
    raise NotImplementedError("Entity extraction not yet implemented (Phase 2)")


def _phase_cluster() -> None:
    raise NotImplementedError("Clustering not yet implemented (Phase 2)")


def _phase_brief() -> None:
    raise NotImplementedError("Brief not yet implemented (Phase 3)")
