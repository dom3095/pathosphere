"""
Tests for the nightly cycle orchestrator: dry_run, from_phase, error handling.
No real I/O calls — real phases mocked where needed.
"""

from unittest.mock import patch


from pathosphere.cycle.orchestrator import (
    PHASE_ORDER,
    Phase,
    CycleState,
    run_cycle,
)


# ─────────────────────────────────────────────────────────────
# Constants and structure
# ─────────────────────────────────────────────────────────────

def test_phase_order_has_six_phases():
    assert len(PHASE_ORDER) == 6


def test_phase_order_sequence():
    assert PHASE_ORDER == [
        Phase.INGEST,
        Phase.EMBED,
        Phase.EXTRACT,
        Phase.CLUSTER,
        Phase.GRAPH,
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
    """With dry_run=True no real phase function must be called."""
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
# Error handling (real phases that raise exceptions)
# ─────────────────────────────────────────────────────────────

def test_ingest_error_stops_cycle():
    """If _phase_ingest raises, the cycle stops and records the error."""
    with patch(
        "pathosphere.cycle.orchestrator._phase_ingest",
        side_effect=RuntimeError("DB connection failed"),
    ):
        state = run_cycle(dry_run=False)
    assert Phase.INGEST in state.errors
    assert "DB connection failed" in state.errors[Phase.INGEST]
    assert Phase.EMBED not in state.completed


def test_error_stops_subsequent_phases():
    """After error in EMBED, EXTRACT/CLUSTER/BRIEF must not run."""
    with patch("pathosphere.cycle.orchestrator._phase_ingest"), \
         patch("pathosphere.cycle.orchestrator._phase_embed",
               side_effect=RuntimeError("embed failed")):
        state = run_cycle(dry_run=False)
    assert Phase.EXTRACT not in state.completed
    assert Phase.CLUSTER not in state.completed
    assert Phase.BRIEF not in state.completed


def test_phase_error_message_stored(capsys):
    """The error message must be saved in state.errors."""
    with patch("pathosphere.cycle.orchestrator._phase_ingest"), \
         patch("pathosphere.cycle.orchestrator._phase_embed",
               side_effect=RuntimeError("embed msg")):
        state = run_cycle(dry_run=False)
    assert isinstance(state.errors[Phase.EMBED], str)
    assert len(state.errors[Phase.EMBED]) > 0


# ─────────────────────────────────────────────────────────────
# Combination from_phase + errors
# ─────────────────────────────────────────────────────────────

def test_from_phase_embed_runs_embed():
    """Resume from EMBED: INGEST skipped, EMBED and later phases run."""
    state = run_cycle(start_from=Phase.EMBED, dry_run=True)
    assert Phase.INGEST not in state.completed
    assert Phase.EMBED in state.completed
    assert Phase.BRIEF in state.completed
