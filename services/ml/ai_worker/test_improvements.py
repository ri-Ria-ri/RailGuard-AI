"""
Tests for the explainable AI risk scoring improvements.

Verifies that:
- Features capture meaningful domain patterns (not arbitrary normalisation)
- ExplainableRiskScorer produces expected scores and contributions
- Factor interpretations are human-readable
- Edge-cases (empty state, missing data) are handled gracefully
"""

import sys
import os
import time
from collections import deque

# Allow running from the ai_worker directory directly.
sys.path.insert(0, os.path.dirname(__file__))

from feature_computer import FeatureComputer
from risk_scorer import ExplainableRiskScorer, FEATURE_WEIGHTS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = time.time()


def make_alerts(severities, age_seconds=0):
    """Build a deque of alert dicts with the given severities."""
    dq = deque()
    for sev in severities:
        dq.append({"ts": NOW - age_seconds, "severity": sev})
    return dq


# ---------------------------------------------------------------------------
# FeatureComputer tests
# ---------------------------------------------------------------------------

def test_crowd_pressure_normal():
    val, label = FeatureComputer.compute_crowd_pressure(30.0)
    assert label == "normal", f"Expected 'normal', got '{label}'"
    assert 0.0 <= val <= 0.25, f"Normal crowd should map to [0, 0.25], got {val}"


def test_crowd_pressure_crowded():
    val, label = FeatureComputer.compute_crowd_pressure(65.0)
    assert label == "crowded", f"Expected 'crowded', got '{label}'"
    assert 0.25 < val <= 0.65, f"Crowded crowd should map to (0.25, 0.65], got {val}"


def test_crowd_pressure_critical():
    val, label = FeatureComputer.compute_crowd_pressure(90.0)
    assert label == "critical", f"Expected 'critical', got '{label}'"
    assert val > 0.65, f"Critical crowd should map above 0.65, got {val}"


def test_crowd_pressure_non_linear():
    # Risk jump from 80% to 90% should be larger than from 20% to 30%.
    jump_low, _ = FeatureComputer.compute_crowd_pressure(30.0)
    jump_low_base, _ = FeatureComputer.compute_crowd_pressure(20.0)
    jump_critical, _ = FeatureComputer.compute_crowd_pressure(90.0)
    jump_critical_base, _ = FeatureComputer.compute_crowd_pressure(80.0)
    assert (jump_critical - jump_critical_base) > (jump_low - jump_low_base), (
        "Critical-zone pressure increment should exceed normal-zone increment"
    )


def test_crowd_pressure_none():
    val, label = FeatureComputer.compute_crowd_pressure(None)
    assert val == 0.0
    assert label == "unknown"


def test_alert_severity_empty():
    val, label = FeatureComputer.compute_alert_severity(deque())
    assert val == 0.0
    assert label == "none"


def test_alert_severity_max_not_latest():
    # HIGH came first, LOW is the latest – should still return HIGH.
    alerts = make_alerts(["HIGH", "LOW"])
    val, label = FeatureComputer.compute_alert_severity(alerts)
    assert val == 1.0
    assert label == "high"


def test_alert_velocity_none_when_old():
    # Alerts older than the velocity window should contribute 0 velocity.
    alerts = make_alerts(["HIGH", "HIGH", "HIGH"], age_seconds=120)
    val, label = FeatureComputer.compute_alert_velocity(alerts, NOW, velocity_window_seconds=60)
    assert val == 0.0, f"Old alerts should produce zero velocity, got {val}"
    assert label == "stable", f"Zero velocity should be labelled 'stable', got '{label}'"


def test_alert_velocity_spiking():
    alerts = make_alerts(["HIGH"] * 5, age_seconds=10)
    val, label = FeatureComputer.compute_alert_velocity(alerts, NOW, velocity_window_seconds=60)
    assert val == 1.0
    assert label == "spiking"


def test_alert_clustering_systemic():
    alerts = make_alerts(["HIGH", "HIGH", "HIGH", "HIGH", "HIGH"])
    val, label = FeatureComputer.compute_alert_clustering(alerts)
    assert label == "systemic"
    assert val > 0.6


def test_alert_clustering_mixed():
    alerts = make_alerts(["HIGH", "MEDIUM", "LOW", "HIGH", "MEDIUM"])
    val, label = FeatureComputer.compute_alert_clustering(alerts)
    assert label == "mixed"


def test_alert_clustering_single():
    alerts = make_alerts(["HIGH"])
    val, label = FeatureComputer.compute_alert_clustering(alerts)
    assert val == 0.0
    assert label == "none"


def test_delay_severity_minor():
    val, label = FeatureComputer.compute_delay_severity(3.0)
    assert label == "minor"
    assert 0.0 <= val <= 0.2


def test_delay_severity_moderate():
    val, label = FeatureComputer.compute_delay_severity(10.0)
    assert label == "moderate"
    assert 0.2 < val <= 0.6


def test_delay_severity_severe():
    val, label = FeatureComputer.compute_delay_severity(20.0)
    assert label == "severe"
    assert 0.6 < val <= 0.85


def test_delay_severity_critical():
    val, label = FeatureComputer.compute_delay_severity(40.0)
    assert label == "critical"
    assert val > 0.85


def test_delay_severity_none():
    val, label = FeatureComputer.compute_delay_severity(None)
    assert val == 0.0
    assert label == "none"


# ---------------------------------------------------------------------------
# ExplainableRiskScorer tests
# ---------------------------------------------------------------------------

scorer = ExplainableRiskScorer()


def test_score_zero_when_no_data():
    features = FeatureComputer.compute_all(deque(), {}, {}, NOW)
    score, contribs = scorer.compute_score(features)
    assert score == 0.0
    assert all(v == 0.0 for v in contribs.values())


def test_score_in_range():
    alerts = make_alerts(["HIGH", "HIGH", "HIGH", "HIGH", "HIGH"], age_seconds=10)
    crowd = {"densityPercent": 95}
    train = {"delayMinutes": 35}
    features = FeatureComputer.compute_all(alerts, crowd, train, NOW)
    score, _ = scorer.compute_score(features)
    assert 0.0 <= score <= 1.0, f"Score out of range: {score}"


def test_score_increases_with_severity():
    # High-risk scenario should score higher than low-risk.
    features_low = FeatureComputer.compute_all(
        make_alerts(["LOW"]), {"densityPercent": 20}, {"delayMinutes": 2}, NOW
    )
    features_high = FeatureComputer.compute_all(
        make_alerts(["HIGH"] * 5, age_seconds=10),
        {"densityPercent": 90},
        {"delayMinutes": 40},
        NOW,
    )
    score_low, _ = scorer.compute_score(features_low)
    score_high, _ = scorer.compute_score(features_high)
    assert score_high > score_low, (
        f"High-risk scenario ({score_high}) should score above low-risk ({score_low})"
    )


def test_contributions_sum_to_score():
    alerts = make_alerts(["MEDIUM", "HIGH"], age_seconds=5)
    crowd = {"densityPercent": 70}
    train = {"delayMinutes": 12}
    features = FeatureComputer.compute_all(alerts, crowd, train, NOW)
    score, contribs = scorer.compute_score(features)
    total = round(sum(contribs.values()), 3)
    assert abs(total - score) < 0.01, f"Sum of contributions {total} should equal score {score}"


def test_weights_sum_to_one():
    assert abs(sum(FEATURE_WEIGHTS.values()) - 1.0) < 1e-9, "Feature weights must sum to 1.0"


def test_risk_level_thresholds():
    assert scorer.risk_level(0.0) == "LOW"
    assert scorer.risk_level(0.39) == "LOW"
    assert scorer.risk_level(0.40) == "MEDIUM"
    assert scorer.risk_level(0.74) == "MEDIUM"
    assert scorer.risk_level(0.75) == "HIGH"
    assert scorer.risk_level(1.0) == "HIGH"


def test_explain_factors_has_interpretation():
    alerts = make_alerts(["HIGH"] * 3, age_seconds=20)
    crowd = {"densityPercent": 85}
    train = {"delayMinutes": 25}
    features = FeatureComputer.compute_all(alerts, crowd, train, NOW)
    score, contribs = scorer.compute_score(features)
    factors = scorer.explain_factors(features, contribs)
    assert len(factors) > 0
    for f in factors:
        assert "interpretation" in f, f"Factor missing 'interpretation': {f}"
        assert "label" in f, f"Factor missing 'label': {f}"
        assert isinstance(f["interpretation"], str) and f["interpretation"], (
            f"Interpretation should be a non-empty string: {f}"
        )


def test_explain_factors_sorted_by_contribution():
    alerts = make_alerts(["HIGH"] * 5, age_seconds=5)
    crowd = {"densityPercent": 90}
    train = {"delayMinutes": 30}
    features = FeatureComputer.compute_all(alerts, crowd, train, NOW)
    score, contribs = scorer.compute_score(features)
    factors = scorer.explain_factors(features, contribs)
    contributions = [f["contribution"] for f in factors]
    assert contributions == sorted(contributions, reverse=True), (
        "Factors should be sorted by contribution (highest first)"
    )


def test_confidence_increases_with_data():
    conf_empty = scorer.compute_confidence(
        FeatureComputer.compute_all(deque(), {}, {}, NOW)
    )
    conf_full = scorer.compute_confidence(
        FeatureComputer.compute_all(
            make_alerts(["HIGH"], age_seconds=10),
            {"densityPercent": 80},
            {"delayMinutes": 20},
            NOW,
        )
    )
    assert conf_full > conf_empty, (
        f"Full-data confidence ({conf_full}) should exceed empty-data confidence ({conf_empty})"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {fn.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(tests)} tests.")
    if failed:
        sys.exit(1)
