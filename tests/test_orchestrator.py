"""
Test orchestratore ciclo notturno: dry_run, from_phase, gestione errori.
Nessuna chiamata I/O reale — fasi reali mockata dove necessario.
"""

from unittest.mock import patch

import pytest

from pathosphere.cycle.orchestrator import (
    PHASE_ORDER,
    Phase,
    CycleState,
    run_cycle,
)


# ─────────────────────────────────────────────────────────────
# Costanti e struttura
# ─────────────────────────────────────────────────────────────

def test_phase_order_has_five_phases():
    assert len(PHASE_ORDER) == 5


def test_phase_order_sequence():
    assert PHASE_ORDER == [
        Phase.INGEST,
        Phase.EMBED,
        Phase.EXTRACT,
        Phase.CLUSTER,
        Phase.BRIEF,
    ]


def test_cycle_state_initial_empty():
    state = CycleState()
    assert state.completed == set()
    assert state.errors == {}


# ─────────────────────────────────────────────────────────────
# dry_run
# ─────────────────────────────────────────────────────────────

def test_dry_run_completes_all_phases():
    state = run_cycle(dry_run=True)
    assert state.completed == set(PHASE_ORDER)
    assert state.errors == {}


def test_dry_run_no_real_phase_called():
    """Con dry_run=True nessuna funzione di fase reale deve essere chiamata."""
    with patch("pathosphere.cycle.orchestrator._phase_ingest") as mock_ingest:
        run_cycle(dry_run=True)
        mock_ingest.assert_not_called()


# ─────────────────────────────────────────────────────────────
# from_phase (skip)
# ─────────────────────────────────────────────────────────────

def test_from_phase_ingest_runs_all(capsys):
    state = run_cycle(start_from=Phase.INGEST, dry_run=True)
    assert state.completed == set(PHASE_ORDER)


def test_from_phase_embed_skips_ingest():
    state = run_cycle(start_from=Phase.EMBED, dry_run=True)
    assert Phase.INGEST not in state.completed
    assert Phase.EMBED in state.completed
    assert Phase.BRIEF in state.completed


def test_from_phase_cluster_skips_first_three():
    state = run_cycle(start_from=Phase.CLUSTER, dry_run=True)
    skipped = {Phase.INGEST, Phase.EMBED, Phase.EXTRACT}
    assert not skipped.intersection(state.completed)
    assert Phase.CLUSTER in state.completed
    assert Phase.BRIEF in state.completed


def test_from_phase_brief_runs_only_brief():
    state = run_cycle(start_from=Phase.BRIEF, dry_run=True)
    assert state.completed == {Phase.BRIEF}


# ─────────────────────────────────────────────────────────────
# Gestione errori (fasi reali che sollevano eccezioni)
# ─────────────────────────────────────────────────────────────

def test_ingest_error_stops_cycle():
    """Se _phase_ingest solleva, il ciclo si ferma e registra l'errore."""
    with patch(
        "pathosphere.cycle.orchestrator._phase_ingest",
        side_effect=RuntimeError("connessione DB fallita"),
    ):
        state = run_cycle(dry_run=False)
    assert Phase.INGEST in state.errors
    assert "connessione DB fallita" in state.errors[Phase.INGEST]
    assert Phase.EMBED not in state.completed


def test_embed_phase_raises_not_implemented():
    """_phase_embed è stub: NotImplementedError registrata in state.errors."""
    with patch("pathosphere.cycle.orchestrator._phase_ingest"):
        state = run_cycle(dry_run=False)
    assert Phase.INGEST in state.completed
    assert Phase.EMBED in state.errors
    assert "NotImplementedError" in state.errors[Phase.EMBED] or \
           "Embedding non ancora" in state.errors[Phase.EMBED]


def test_error_stops_subsequent_phases():
    """Dopo errore in EMBED, EXTRACT/CLUSTER/BRIEF non devono girare."""
    with patch("pathosphere.cycle.orchestrator._phase_ingest"):
        state = run_cycle(dry_run=False)
    assert Phase.EXTRACT not in state.completed
    assert Phase.CLUSTER not in state.completed
    assert Phase.BRIEF not in state.completed


def test_phase_error_message_stored(capsys):
    """Il messaggio di errore deve essere salvato in state.errors."""
    with patch("pathosphere.cycle.orchestrator._phase_ingest"):
        state = run_cycle(dry_run=False)
    assert isinstance(state.errors[Phase.EMBED], str)
    assert len(state.errors[Phase.EMBED]) > 0


# ─────────────────────────────────────────────────────────────
# Combinazione from_phase + errori
# ─────────────────────────────────────────────────────────────

def test_from_phase_embed_hits_not_implemented():
    """Riprendere da EMBED: INGEST skippato, EMBED fallisce per NotImplementedError."""
    state = run_cycle(start_from=Phase.EMBED, dry_run=False)
    assert Phase.INGEST not in state.completed
    assert Phase.EMBED in state.errors
