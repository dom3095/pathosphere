"""
Orchestratore del ciclo notturno.

Fasi (sequenziali, riprendibili):
  1. ingest   — scarica documenti dalle fonti
  2. embed    — calcola embeddings + dedup vettoriale
  3. extract  — NER, geocoding, entity linking
  4. cluster  — raggruppa articoli in eventi
  5. brief    — genera brief mattutino con Qwen

Ogni fase è atomica: se interrotta, riprende dall'ultima completata.
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
    """Esegue il ciclo notturno in modo sequenziale e riprendibile."""
    state = CycleState()
    skip = start_from is not None

    for phase in PHASE_ORDER:
        if skip:
            if phase == start_from:
                skip = False
            else:
                logger.info(f"Salto fase {phase.name} (ripresa da {start_from.name})")
                continue

        logger.info(f"→ Fase {phase.name}")

        if dry_run:
            logger.info(f"  [dry-run] {phase.name} simulata")
            state.completed.add(phase)
            continue

        try:
            _run_phase(phase)
            state.completed.add(phase)
            logger.success(f"✓ {phase.name} completata")
        except Exception as exc:
            state.errors[phase] = str(exc)
            logger.error(f"✗ {phase.name} fallita: {exc}")
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
        f"INGEST: {result.events_inserted} eventi, {result.docs_inserted} doc"
    )


def _phase_embed() -> None:
    raise NotImplementedError("Embedding non ancora implementato (Fase 2)")


def _phase_extract() -> None:
    raise NotImplementedError("Estrazione entità non ancora implementata (Fase 2)")


def _phase_cluster() -> None:
    raise NotImplementedError("Clustering non ancora implementato (Fase 2)")


def _phase_brief() -> None:
    raise NotImplementedError("Brief non ancora implementato (Fase 3)")
