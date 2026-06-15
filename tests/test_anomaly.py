"""Unit tests for the shared trailing-baseline anomaly detector."""

from pathosphere.ingest.anomaly import find_anomalies

# noisy baseline around ~40 (stdev > 0)
_BASELINE = [38, 41, 39, 42, 40, 37, 43, 40, 38, 41,
             40, 39, 42, 38, 41, 40, 37, 43, 39, 40]


def _points(values: list[float]) -> list[dict]:
    return [{"date": f"d{i:03d}", "n": v} for i, v in enumerate(values)]


def test_latest_only_flags_last_point():
    pts = _points(_BASELINE + [120])
    out = find_anomalies(pts, value_key="n", baseline_days=30,
                         z_threshold=2.0, whole_history=False)
    assert len(out) == 1
    assert out[0].point["date"] == f"d{len(_BASELINE):03d}"
    assert out[0].z > 0


def test_latest_only_ignores_mid_history_spike():
    # spike in the middle, quiet last point → latest-only sees nothing
    values = _BASELINE + [200] + _BASELINE
    out = find_anomalies(_points(values), value_key="n", baseline_days=30,
                         z_threshold=2.0, whole_history=False)
    assert out == []


def test_whole_history_recovers_mid_spike():
    values = _BASELINE + [200] + _BASELINE
    out = find_anomalies(_points(values), value_key="n", baseline_days=30,
                         z_threshold=2.0, whole_history=True)
    assert len(out) == 1
    assert out[0].point["date"] == f"d{len(_BASELINE):03d}"   # the spike index


def test_whole_history_multiple_anomalies():
    # spikes spaced > baseline_days apart so neither contaminates the other's
    # trailing window (a 30d-window baseline must stay clean for the drop)
    values = _BASELINE + [200] + _BASELINE * 2 + [5] + _BASELINE
    out = find_anomalies(_points(values), value_key="n", baseline_days=30,
                         z_threshold=2.0, direction="both", whole_history=True)
    assert len(out) == 2   # one surge, one drop


def test_surge_direction_ignores_drops():
    values = _BASELINE + [0]   # sharp drop
    out = find_anomalies(_points(values), value_key="n", baseline_days=30,
                         z_threshold=2.0, direction="surge", whole_history=True)
    assert out == []


def test_min_value_floor_blocks_low_counts():
    # statistically high but below the absolute floor
    low = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2]
    out = find_anomalies(_points(low + [20]), value_key="n", baseline_days=30,
                         z_threshold=2.0, direction="surge", min_value=50,
                         whole_history=True)
    assert out == []


def test_insufficient_baseline_skipped():
    out = find_anomalies(_points([40, 41, 200]), value_key="n",
                         baseline_days=30, z_threshold=2.0, whole_history=True)
    assert out == []


def test_zero_stdev_baseline_skipped():
    out = find_anomalies(_points([40] * 20 + [200]), value_key="n",
                         baseline_days=30, z_threshold=2.0, whole_history=True)
    assert out == []
