# AI Scoring Improvements

## Overview

The previous risk-scoring model was a simple weighted sum of normalised sensor
values with no statistical justification:

```python
# Before
score = 0.45*crowdNorm + 0.25*alertNorm + 0.20*delayNorm + 0.10*alertRateNorm
```

This has been replaced with **explainable feature engineering** backed by
railway safety domain knowledge.  Every number now has a documented rationale,
and each score can be explained in plain language.

---

## What Changed

### New files

| File | Purpose |
|------|---------|
| `services/ml/ai_worker/feature_computer.py` | `FeatureComputer` – domain-aware feature engineering |
| `services/ml/ai_worker/risk_scorer.py` | `ExplainableRiskScorer` – scoring with contribution tracking |
| `services/ml/ai_worker/test_improvements.py` | 26 unit tests covering all features and scorer behaviour |

### Modified files

| File | Change |
|------|--------|
| `services/ml/ai_worker/main.py` | Replaced hardcoded formula with `FeatureComputer` + `ExplainableRiskScorer` |
| `services/backend/app/main.py` | Added `interpretation` field to `RiskResponse`; backend now builds a plain-English summary from `topFactors` |
| `services/frontend/src/App.jsx` | Risk panel now shows `interpretation` text (e.g. *"Crowd density critical (contributing 0.28)"*) falling back to the old `factor: contribution%` format for backward compatibility |

---

## Feature Engineering

### 1. `crowd_pressure` (weight 0.30)

**Why:** Crowd risk is non-linear.  The difference between 80 % and 90 %
density is far more dangerous than between 20 % and 30 %.  A piecewise
function captures the three operational zones used by station safety teams.

| Density range | Mapped value | Category |
|--------------|-------------|----------|
| 0 – 50 % | 0.00 – 0.25 | `normal` |
| 50 – 80 % | 0.25 – 0.65 | `crowded` |
| 80 – 100 % | 0.65 – 1.00 | `critical` |

### 2. `alert_severity` (weight 0.25)

**Why:** Uses the **maximum** severity in the sliding window, not the most
recent alert.  A danger that triggered a HIGH alert does not disappear just
because a subsequent LOW alert arrived.

| Severity | Value |
|---------|-------|
| LOW | 0.2 |
| MEDIUM | 0.6 |
| HIGH / CRITICAL | 1.0 |

### 3. `alert_velocity` (weight 0.20)

**Why:** A zone with 5 alerts in the last minute is actively worsening even if
each individual alert is only MEDIUM severity.  This captures the *trend*,
enabling early warning before severity escalates.

Normalised so that ≥ 5 alerts per 60 seconds maps to 1.0.

| Rate | Value | Label |
|------|-------|-------|
| 0 alerts / min | 0.0 | `stable` |
| 2 alerts / min | 0.4 | `rising` |
| 4+ alerts / min | 0.8 – 1.0 | `spiking` |

### 4. `delay_severity` (weight 0.15)

**Why:** Uses the operational delay thresholds agreed by railway operators
rather than a linear cap at 30 minutes.

| Delay | Value | Category |
|-------|-------|----------|
| 0 – 5 min | 0.00 – 0.20 | `minor` |
| 5 – 15 min | 0.20 – 0.60 | `moderate` |
| 15 – 30 min | 0.60 – 0.85 | `severe` |
| 30+ min | 0.85 – 1.00 | `critical` |

### 5. `alert_clustering` (weight 0.10)

**Why:** When the same alert severity repeats many times it indicates a
**systemic** issue (e.g. a recurring fault or environmental cause) rather than
a one-off incident.  Systemic issues deserve a higher risk contribution.

| Dominant-type fraction | Label |
|----------------------|-------|
| ≥ 80 % | `systemic` |
| < 80 % | `mixed` |

---

## Scoring Formula

```
score = Σ (feature_value × weight)   clamped to [0, 1]

weights:
  crowd_pressure   0.30
  alert_severity   0.25
  alert_velocity   0.20
  delay_severity   0.15
  alert_clustering 0.10
  ─────────────────────
  total            1.00
```

Because every weight is explicitly documented and each feature value is bounded
to [0, 1], the score is the **sum of contributions** and can be fully
decomposed for any zone.

---

## Explainability

### API response (`topFactors`)

Each factor in `topFactors` now includes:

```json
{
  "factor":         "crowd_pressure",
  "contribution":   0.254,
  "label":          "Crowd density",
  "interpretation": "Crowd density critical (contributing 0.254)"
}
```

### API response (`interpretation`)

The backend joins the top-3 interpretations into a single string:

```
"Crowd density critical (contributing 0.25); Alert rate spiking (contributing 0.20); Alert severity high (contributing 0.22)"
```

### Dashboard

The frontend risk panel now shows the `interpretation` string for each factor
instead of the raw `factor: contribution%` format.  If the backend sends an
older payload (without `interpretation`), it falls back gracefully to the
previous display format.

---

## Confidence Score

Confidence now reflects **data-source completeness**:

```
confidence = 0.60 + 0.40 × (features_with_data / 5)
```

A zone with all five features populated gets full confidence (1.0); a zone
with only crowd data (no alerts, no train) gets 0.68.  The baseline of 0.60
means partial data still yields a usable score.

---

## Backward Compatibility

- `riskScore`, `riskLevel`, `confidence`, `modelName`, `modelVersion`, and
  `timestamp` fields are unchanged.
- `topFactors` gains two extra fields (`label`, `interpretation`) but the
  existing `factor` and `contribution` fields remain.
- A new `interpretation` string field is added to the API response (default
  `""`).
- The frontend falls back to the old display format when `interpretation` is
  absent.

---

## Upgrade Path

The `ExplainableRiskScorer` is designed as a drop-in replacement for a future
ML model (e.g. XGBoost + SHAP).  When a trained model is available:

1. Replace the `compute_score` method with model inference.
2. Replace `explain_factors` with SHAP values.
3. Keep the same output contract – the rest of the system requires no changes.
