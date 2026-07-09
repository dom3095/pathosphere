"""Tests for the autonomous cycle loop with state persistence."""

import json
import tempfile
from pathlib import Path
from datetime import datetime

from pathosphere.cycle.loop import LoopState
from pathosphere.cycle.orchestrator import Phase


def test_loop_state_load_save_empty():
    """Empty state file loads as defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        mgr = LoopState(state_file)

        state = mgr.load()
        assert state["last_phase"] is None
        assert state["last_completion"] is None
        assert state["error_log"] == []


def test_loop_state_save_and_load():
    """State persists correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        mgr = LoopState(state_file)

        now = datetime.utcnow()
        error_log = [{"timestamp": now.isoformat(), "phase": "EMBED", "error": "out of memory"}]

        mgr.save(Phase.EXTRACT, now, error_log)
        loaded = mgr.load()

        assert loaded["last_phase"] == "EXTRACT"
        assert loaded["last_completion"] == now.isoformat()
        assert len(loaded["error_log"]) == 1
        assert loaded["error_log"][0]["phase"] == "EMBED"


def test_loop_state_nonexistent_file():
    """Loading from nonexistent file returns defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "nonexistent.json"
        mgr = LoopState(state_file)

        state = mgr.load()
        assert state["last_phase"] is None


def test_loop_state_corrupted_json():
    """Corrupted JSON file returns defaults with warning."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state_file.write_text("{ invalid json }")
        mgr = LoopState(state_file)

        state = mgr.load()
        assert state["last_phase"] is None


def test_loop_state_next_phase_after_none():
    """Starting from no phase returns INGEST."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        mgr = LoopState(state_file)

        next_phase = mgr.next_phase_after(None)
        assert next_phase == Phase.INGEST


def test_loop_state_next_phase_after_ingest():
    """After INGEST comes EMBED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        mgr = LoopState(state_file)

        next_phase = mgr.next_phase_after(Phase.INGEST)
        assert next_phase == Phase.EMBED


def test_loop_state_next_phase_after_brief():
    """After BRIEF (cycle complete) restart from INGEST."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        mgr = LoopState(state_file)

        next_phase = mgr.next_phase_after(Phase.BRIEF)
        assert next_phase == Phase.INGEST


def test_loop_state_error_log_truncation():
    """Error log keeps only last 100 entries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        mgr = LoopState(state_file)

        # Create 150 error entries
        error_log = [
            {"timestamp": f"2026-07-10T{i:02d}:00:00", "phase": "INGEST", "error": f"error {i}"}
            for i in range(150)
        ]

        mgr.save(Phase.INGEST, datetime.utcnow(), error_log)
        loaded = mgr.load()

        assert len(loaded["error_log"]) == 100
        # Latest entries are kept
        assert loaded["error_log"][0]["error"] == "error 50"
        assert loaded["error_log"][-1]["error"] == "error 149"
