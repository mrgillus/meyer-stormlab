# StormLab ML operational readiness

Keep live deterministic guidance distinct from research forecasts until independent verification is established.

## Current engineering sequence

1. Retrain existing GEFS models using all available archive shards. Independent scheduled trainer checks input fingerprints and skips unchanged data.
2. Evaluate fixed 48-hour windows from the same initialization, with both existing observation-date mappings. This reduces sensitivity to within-window redistribution but does not resolve reporting clocks.
3. Obtain independent, timestamped snow observations and documented accumulation intervals. Preserve sources, station/grid matching, and pre-event forecast issue times.
4. Expand atmospheric profile training and test physical/learned hybrid models on new winter seasons.
5. Archive prospective predictions for all named events, including failures, missing inputs and model-version metadata.
6. Validate probability calibration and operational skill against simple baselines on a genuinely untouched evaluation set. No automatic promotion from experimental reports.

## Scheduling

Regional training runs daily and on relevant code updates. The fixed 48-hour research benchmark runs daily and on code updates. GitHub Actions is the unattended execution layer. A successful planning job alone is not evidence of training or publication.

## Continuous shadow inference

Select a recent 00Z cycle with an eight-hour availability lag, evaluate unexpired lead-matched +24/+48/+72/+168h windows, and archive incomplete attempts as well as successful ones. Runs every six hours. Consult status.json for latest attempt and report.json for last complete run. Research only; no automatic promotion.

Shadow inference also runs when "Regional GEFS snowfall learning" completes successfully, but only if the last published status predates the current training reports (`--if-stale`). Each mapping records the SHA-256 of the exact `.joblib` artifact it used. Every out-of-range flag carries the feature name, units, live value and training bounds (`outside_training_detail`, plus a per-mapping `out_of_range_summary`). The bounds are never widened by inference code.

## Finding: +24h feature 103 out-of-range flag (2026-11-26)

Feature 103 in feature_version 1 is the 5-member ensemble standard deviation of 500 mb height at synoptic_5 (38°N, 82°W). It is a shared synoptic feature, so one exceedance flags every location.

- Training bounds at +24h: 0.51–20.05 gpm. Over all 3,915 complete archive initializations, +24h spread there has median 4.10, 99th percentile 12.79, maximum 21.0 gpm.
- 2026-11-26 00Z operational +24h: 27.38 gpm, from a genuine 0.5° `HGT 500 mb` record (members 5465–5548 gpm, c00 5503). Units and decoding match the archive (gpm). An independent re-extraction reproduced the value.
- Interpretation: a real, record-large short-range position uncertainty of the Thanksgiving trough over the Ohio Valley, not an extraction or unit error. The +24h shadow outputs for this cycle are correctly flagged as extrapolation.
- Open question: operational GEFS uses 30 perturbed members, of which p01–p04 are taken, while the reforecast had only 4. Whether p01–p04 spread is statistically comparable between the two systems has not been tested. Compare operational and reforecast spread climatology over a full winter before treating spread features as compatible.
