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
