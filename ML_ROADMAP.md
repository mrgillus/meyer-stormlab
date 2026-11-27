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

## Event-driven storm pipelines

The matched-hour storm blend (`storm_blend.py`, hourly) and rapid-refresh shadow inference (`rapid_learning.py live`, hourly) take their window from the named-event catalog via `scripts/event_windows.py`. They no longer use hardcoded dates.

- Only non-routine winter modes (`snow`, `ice`) in the latest capture open a window, and only within 5 days. Flurries (`light_snow`), rain and freezing temperatures alone do not.
- A new window needs a fresh catalog (`status: current`). A window never starts in the past: if the event is already underway when first seen, it starts at the current hour.
- The selected window is pinned in `data/storm/window.json`. Its start never moves. Its end extends only when the same named event lengthens, capped at 120 hours. This keeps frozen elapsed-hour forecasts valid.
- With no active window, the scheduled jobs exit without touching published reports.
- The Thanksgiving window was seeded into `window.json` so its final hours carry over unchanged.

Thanksgiving-only case-study scripts still hold fixed dates by design: `regional_live.py`, `regional_precip_audit.py`, `atmospheric_profiles.py` (live mode) and `temporal_profiles.py`. They run only on manual dispatch or on edits to their own files. The duplicate live-inference step in `gefs48.yml` was removed. Live shadow inference is owned by `regional-live-v2.yml`.

## Clock-independent historical storm-total labels (2026-11-27)

`scripts/storm_total_labels.py` builds event-total snowfall labels from the 2000–2019 archive without assuming any station observation clock (`data/storm_total_labels/`).

- **Selection.** Storms are runs of 1–3 UTC days where the 5-member GEFS +24h precipitation is ≥1 mm, with ≤0.25 mm on the two days before and after. Selection is forecast-only.
- **Label.** The sum of station reports dated S-1..E+1. That covers the UTC window for any clock between 0 and 24h.
- **Sample size (defaults).** Station-events by split (train / validation / test): 568 / 326 / 577. But only **41 / 13 / 9** distinct storm windows reached ≥1" at some station, and **16 / 4 / 2** reached ≥4". The held-out sample is too small to evaluate heavy-snow skill.
- **Why it's small.** Most candidates fail for missing station reports (3,570 rejected vs 1,471 kept). Looser thresholds roughly double the counts at the cost of a larger clock-ambiguity bound; see `threshold_sensitivity`.
- **Finding.** On storms with ≥1" observed, the legacy daily mappings undercount the storm total by an average of 1.38" (`utc_end_date`) and 0.88" (`utc_end_previous_date`). They miss more than half the total in 44% and 24% of those storms. Daily-label training and evaluation systematically understate multi-day storm totals. That likely contributes to the reported heavy-snow underprediction and should be considered before tuning models against daily labels.

### Re-scoring existing models against storm totals

`scripts/storm_total_rescore.py` sums the existing daily RF/boosted predictions over each held-out storm, from one init. It compares them with the clock-independent total and with the legacy daily label each model was trained on. No model is refit (`data/storm_total_rescore/`).

Validation + test seasons: 903 station-storms, 154 windows. For ≥4" storms (27 station-storms, only 6 windows):

- The true storm total averages 7.44". Legacy daily labels capture only 4.40" (`utc_end_date`) or 5.11" (`utc_end_previous_date`).
- RF predicts 1.39" / 2.09" (19% / 28% of the true total). Boosted predicts 0.58" / 0.93". The physical 10:1 cold-precipitation reference predicts 5.19" (70%).
- So roughly half of the heavy-snow underprediction is label misalignment (2.3–3.0"). The rest (~3.0") is model shortfall even against the models' own labels, consistent with tree models not extrapolating.
- For 1–4" storms, most of the apparent error is label misalignment.
- Both tree models over-forecast non-snow storms by 0.06–0.31".

Implication: train and evaluate on storm totals, and favor physically anchored hybrids (phase × liquid × snow ratio) over pure tree regression for heavy events. Heavy-event conclusions remain tentative (6 windows).

### Station-dense GEFS extension (in progress)

`scripts/gefs_stations.py` and `.github/workflows/gefs-stations.yml` build a separate archive in `data/gefs_stations/`. Existing archives are untouched.

- **Stations:** every GHCN snowfall station within 75 mi of St. Louis with at least 400 usable 2000–2019 cold-season reports, capped at 150. The list is ranked by report count, never by snowfall amounts.
- **Fields:** 2 m and 850 mb temperature and 24 h precipitation (from exact 6 h blocks) at +24/+48/+72 h, 5 members, for every archive initialization.
- **Points are frozen** by the manifest fingerprint after a one-time `select` step.
- **Purpose:** raise the number of storms with complete station reports for clock-independent storm-total labels. Combine with leave-one-winter-out evaluation, since distinct storms, not stations, limit heavy-snow evaluation.
- **Validated locally:** a real 2011-02-01 extraction reproduced the existing archive's Lambert values exactly. One initialization takes about 11 s for all members.
- **Next, once complete:** extend `storm_total_labels.py` to read this archive, then train and evaluate storm-total models (physical baseline, hybrid, tree) with leave-one-winter-out splits.
