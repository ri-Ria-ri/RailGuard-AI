"""
Domain-aware feature engineering for railway risk scoring.

Rather than simple normalization (value / max_value), each feature
captures a specific risk signal using railway safety domain knowledge.
This makes the features meaningful and the resulting scores explainable.
"""

from collections import Counter, deque
from typing import Any, Deque, Dict, Optional, Tuple

SEVERITY_VALUES: Dict[str, float] = {
    "CRITICAL": 1.0,
    "HIGH": 1.0,
    "MEDIUM": 0.6,
    "LOW": 0.2,
}


class FeatureComputer:
    """
    Computes engineered features from raw zone state (alerts, crowd, train).

    Each feature returns a (value, label) tuple where:
    - value is a float in [0, 1]
    - label is a human-readable category (e.g. "critical", "spiking", "severe")
    """

    @staticmethod
    def compute_crowd_pressure(density_percent: Optional[float]) -> Tuple[float, str]:
        """
        Piecewise crowd pressure using station safety thresholds.

        Crowd risk is non-linear: the jump from 80%→90% is far more dangerous
        than 20%→30%. Three zones:
          - Normal  (0–50%):  maps to [0.00, 0.25]
          - Crowded (50–80%): maps to [0.25, 0.65]
          - Critical (80%+):  maps to [0.65, 1.00]
        """
        if density_percent is None:
            return 0.0, "unknown"
        d = max(0.0, min(100.0, float(density_percent)))
        if d <= 50.0:
            return 0.25 * (d / 50.0), "normal"
        elif d <= 80.0:
            return 0.25 + 0.40 * ((d - 50.0) / 30.0), "crowded"
        else:
            return 0.65 + 0.35 * min(1.0, (d - 80.0) / 20.0), "critical"

    @staticmethod
    def compute_alert_severity(alerts: Deque[Dict[str, Any]]) -> Tuple[float, str]:
        """
        Maximum severity observed in the alert window.

        Using max rather than the latest alert because the hazard has not
        passed just because a lower-severity alert arrived afterward.
        """
        if not alerts:
            return 0.0, "none"
        max_val = max(
            SEVERITY_VALUES.get((a.get("severity") or "LOW").upper(), 0.2)
            for a in alerts
        )
        label = "high" if max_val >= 1.0 else "medium" if max_val >= 0.6 else "low"
        return max_val, label

    @staticmethod
    def compute_alert_velocity(
        alerts: Deque[Dict[str, Any]],
        now_ts: float,
        velocity_window_seconds: int = 60,
    ) -> Tuple[float, str]:
        """
        Rate of alert arrivals in the last `velocity_window_seconds`.

        A rising alert rate signals a worsening situation even when individual
        alert severities look stable.  Normalised so that 5+ alerts per minute
        maps to 1.0 (maximum velocity).
        """
        if not alerts:
            return 0.0, "none"
        cutoff = now_ts - velocity_window_seconds
        recent_count = sum(1 for a in alerts if a.get("ts", 0) >= cutoff)
        value = min(1.0, recent_count / 5.0)
        label = "spiking" if value >= 0.8 else "rising" if value >= 0.4 else "stable"
        return value, label

    @staticmethod
    def compute_alert_clustering(alerts: Deque[Dict[str, Any]]) -> Tuple[float, str]:
        """
        Fraction of alerts sharing the same severity in the window.

        High clustering (same severity repeating) indicates a systemic issue
        rather than a one-off incident.  Returns 0 when there are fewer than
        two alerts (clustering is not meaningful with a single data point).
        """
        if len(alerts) < 2:
            return 0.0, "none"
        severities = [(a.get("severity") or "LOW").upper() for a in alerts]
        most_common_count = Counter(severities).most_common(1)[0][1]
        ratio = most_common_count / len(severities)
        # Only penalise if a clear majority share the same type.
        value = ratio if ratio >= 0.6 else ratio * 0.5
        label = "systemic" if ratio >= 0.8 else "mixed"
        return min(1.0, value), label

    @staticmethod
    def compute_delay_severity(delay_minutes: Optional[float]) -> Tuple[float, str]:
        """
        Piecewise delay severity using railway operational thresholds.

        - Minor   (0–5 min):   maps to [0.00, 0.20]  (within tolerance)
        - Moderate (5–15 min): maps to [0.20, 0.60]  (operationally notable)
        - Severe  (15–30 min): maps to [0.60, 0.85]  (significant disruption)
        - Critical (30+ min):  maps to [0.85, 1.00]  (severe disruption)
        """
        if delay_minutes is None:
            return 0.0, "none"
        d = max(0.0, float(delay_minutes))
        if d <= 5.0:
            return 0.20 * (d / 5.0), "minor"
        elif d <= 15.0:
            return 0.20 + 0.40 * ((d - 5.0) / 10.0), "moderate"
        elif d <= 30.0:
            return 0.60 + 0.25 * ((d - 15.0) / 15.0), "severe"
        else:
            return 0.85 + 0.15 * min(1.0, (d - 30.0) / 30.0), "critical"

    @classmethod
    def compute_all(
        cls,
        alerts: Deque[Dict[str, Any]],
        crowd: Dict[str, Any],
        train: Dict[str, Any],
        now_ts: float,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Compute all engineered features from the raw per-zone state.

        Returns a dict of ``{feature_name: {"value": float, "label": str}}``.
        """
        density_percent = crowd.get("densityPercent")
        delay_minutes = train.get("delayMinutes")

        crowd_pressure, crowd_label = cls.compute_crowd_pressure(density_percent)
        alert_severity, sev_label = cls.compute_alert_severity(alerts)
        alert_velocity, vel_label = cls.compute_alert_velocity(alerts, now_ts)
        alert_clustering, clust_label = cls.compute_alert_clustering(alerts)
        delay_severity, delay_label = cls.compute_delay_severity(delay_minutes)

        return {
            "crowd_pressure": {"value": crowd_pressure, "label": crowd_label},
            "alert_severity": {"value": alert_severity, "label": sev_label},
            "alert_velocity": {"value": alert_velocity, "label": vel_label},
            "alert_clustering": {"value": alert_clustering, "label": clust_label},
            "delay_severity": {"value": delay_severity, "label": delay_label},
        }
