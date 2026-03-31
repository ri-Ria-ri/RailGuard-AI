"""
Explainable risk scorer with per-feature contribution tracking.

Score = sum of (feature_value × weight) for each feature.
This makes it straightforward to explain what drove each risk level:
  "Risk is 0.65 because crowd is critical (0.25), alerts spiking (0.20),
   and delay is severe (0.12)."
"""

from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Feature weights
# ---------------------------------------------------------------------------
# Rationale for each weight:
#   crowd_pressure  0.30 – crowd density is the primary immediate-safety driver
#   alert_severity  0.25 – direct danger signal from the alert stream
#   alert_velocity  0.20 – trend indicator: rising alerts signal worsening state
#   delay_severity  0.15 – operational disruption amplifies crowd pressure
#   alert_clustering 0.10 – systemic-issue amplifier (same type repeating)
# Total: 1.00
FEATURE_WEIGHTS: Dict[str, float] = {
    "crowd_pressure": 0.30,
    "alert_severity": 0.25,
    "alert_velocity": 0.20,
    "delay_severity": 0.15,
    "alert_clustering": 0.10,
}

FEATURE_HUMAN_LABELS: Dict[str, str] = {
    "crowd_pressure": "Crowd density",
    "alert_severity": "Alert severity",
    "alert_velocity": "Alert rate",
    "delay_severity": "Train delay",
    "alert_clustering": "Systemic alerts",
}


class ExplainableRiskScorer:
    """
    Computes risk scores with per-feature contribution tracking.

    Unlike the previous hardcoded weighted formula, every number here has a
    documented purpose.  The ``explain_factors`` method returns human-readable
    interpretations so stakeholders can understand why risk changed.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_score(
        self,
        features: Dict[str, Any],
    ) -> Tuple[float, Dict[str, float]]:
        """
        Compute risk score and per-feature contributions.

        Parameters
        ----------
        features:
            Output of ``FeatureComputer.compute_all``, i.e.
            ``{feature_name: {"value": float, "label": str}}``.

        Returns
        -------
        score:
            Aggregated risk in [0, 1].
        contributions:
            ``{feature_name: contribution_value}`` where each contribution
            equals weight × feature_value.
        """
        contributions: Dict[str, float] = {}
        total = 0.0
        for fname, weight in FEATURE_WEIGHTS.items():
            feat = features.get(fname, {})
            value = feat.get("value", 0.0) if isinstance(feat, dict) else float(feat or 0.0)
            contrib = weight * value
            contributions[fname] = round(contrib, 3)
            total += contrib
        return round(max(0.0, min(1.0, total)), 3), contributions

    def risk_level(self, score: float) -> str:
        """Thresholds kept identical to the previous implementation."""
        if score < 0.40:
            return "LOW"
        if score < 0.75:
            return "MEDIUM"
        return "HIGH"

    def explain_factors(
        self,
        features: Dict[str, Any],
        contributions: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """
        Return the top driving factors with human-readable interpretations.

        Each entry contains:
        - ``factor``         – machine-readable feature name
        - ``contribution``   – numeric contribution to the risk score
        - ``label``          – human-readable feature name
        - ``interpretation`` – natural-language explanation, e.g.
                               "Crowd density critical (contributing 0.25)"
        """
        factors = []
        for fname, contrib in sorted(
            contributions.items(), key=lambda kv: kv[1], reverse=True
        ):
            feat = features.get(fname, {})
            category = feat.get("label", "") if isinstance(feat, dict) else ""
            human_label = FEATURE_HUMAN_LABELS.get(fname, fname)
            interpretation = (
                f"{human_label} {category} (contributing {contrib:.3f})"
                if category and category not in ("none", "unknown")
                else f"{human_label} (contributing {contrib:.3f})"
            )
            factors.append(
                {
                    "factor": fname,
                    "contribution": contrib,
                    "label": human_label,
                    "interpretation": interpretation,
                }
            )
        return factors[:3]

    def compute_confidence(self, features: Dict[str, Any]) -> float:
        """
        Confidence based on data-source completeness.

        Full confidence (1.0) is only possible when all five features carry
        real data.  Missing or unknown features reduce confidence proportionally.
        A baseline of 0.60 is retained so partial data still yields a usable
        (if lower-confidence) score.
        """
        present = sum(
            1
            for f in features.values()
            if isinstance(f, dict)
            and f.get("label") not in (None, "none", "unknown")
        )
        completeness = present / len(FEATURE_WEIGHTS)
        return round(0.60 + 0.40 * completeness, 3)
