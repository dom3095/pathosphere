"""
Shared trailing-baseline anomaly detector for timeseries ingestors.

PortWatch (chokepoint transits) and FIRMS (fire detections) both promote
points that deviate from their trailing baseline into events. Detection lives
here; event construction stays in each ingestor. The baseline for a point uses
only the `baseline_days` points strictly *before* it — no lookahead.

Two regimes:
  whole_history=False → evaluate only the most recent point (incremental run,
    cheap: one z-score per area per night).
  whole_history=True  → sweep every point (one-shot backfill: recovers all the
    historical anomalies that a single latest-only pass would miss).
"""

import statistics
from dataclasses import dataclass

MIN_BASELINE_POINTS = 10


@dataclass
class Anomaly:
    point: dict          # the original row (date + value + extra fields)
    value: float
    mean: float
    stdev: float
    z: float
    pct: float           # percent deviation from baseline mean


def find_anomalies(
    points: list[dict],
    *,
    value_key: str,
    baseline_days: int,
    z_threshold: float,
    direction: str = "both",   # "both" | "surge" | "drop"
    min_value: float = 0.0,    # absolute floor the point must exceed (surge noise guard)
    whole_history: bool = True,
) -> list[Anomaly]:
    """Detect baseline deviations in a date-ascending series (no lookahead).

    points must be ordered oldest→newest. Returns one Anomaly per flagged
    point, in chronological order.
    """
    series = [p for p in points if p.get(value_key) is not None]
    n = len(series)
    if n == 0:
        return []

    start = 0 if whole_history else n - 1
    out: list[Anomaly] = []
    for i in range(start, n):
        lo = max(0, i - baseline_days)
        baseline = [series[j][value_key] for j in range(lo, i)]
        if len(baseline) < MIN_BASELINE_POINTS:
            continue

        value = series[i][value_key]
        if value < min_value:
            continue

        mean = statistics.fmean(baseline)
        stdev = statistics.stdev(baseline)
        if stdev == 0:
            continue

        z = (value - mean) / stdev
        if direction == "surge" and z < z_threshold:
            continue
        if direction == "drop" and z > -z_threshold:
            continue
        if direction == "both" and abs(z) < z_threshold:
            continue

        pct = (value - mean) / mean * 100 if mean else 0.0
        out.append(Anomaly(series[i], value, mean, stdev, z, pct))
    return out
