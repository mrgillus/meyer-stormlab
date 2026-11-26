# StormLab: observed weather and forecast verification

## What has been implemented

A separate, scheduled **observations and verification** pipeline reads the persistent
event cases from \`data/events/catalog.json\`, retrieves real daily climate-station
reports from the **Regional Climate Centers Applied Climate Information System
(RCC-ACIS)**, and associates *reported station data* and a *frozen pre-event model
capture* with each eligible event.

- \`scripts/event_verification.py\`: normalizes raw station reports, preserves provenance,
  flags, source station IDs, observation time, and location.
- \`tests/test_event_verification.py\`: tests trace/missing/multiday flags, station
  metadata, pre-event forecast selection, and fail-closed scoring.
- \`.github/workflows/verify-events.yml\`: runs four times daily after the model
  collector and event tracker; performs tests and writes only \`data/verification/\`.
- \`data/verification/observations.json\`: station metadata and the raw-reported
  daily measurements available at retrieval time.
- \`data/verification/report.json\`: event-specific links to reported station
  snowfall / precipitation and pre-event modeled calendar-day values.
- \`docs/verification.js\`: presents these records under the selected **Storm File**.

## Real observations, not inferred weather

Provider: https://data.rcc-acis.org/MultiStnData

Documentation: https://docs.rcc-acis.org/acisws/

ACIS returns \`snow\` (inches of **new snowfall**) and \`pcpn\` (inches of
**liquid precipitation**), for independent reporting stations. We request the
\`t,f\` (observation time and flags) additions. The data-retrieval timestamp
is preserved separately from each station's reported date.

Daily station observations are **not** observations at the center of a nearby
forecast town. Station coordinates are preserved, and we label the nearest
town and station-to-town distance. They are not a storm-area-average.

\`T\` (trace) is **not** a measured numerical zero. \`M\` (missing) is missing.
An accumulated/multi-day value marked \`A\` cannot be assigned to a single
calendar day. Flagged, negative, or unrecognized data are excluded from
numeric evidence. A reported \`0.00\` with no adverse flags is a measured zero.

## Why scores must remain null for now

The archived model daily values represent forecast calendar days.
Climate stations often observe on a different 24-hour window, such as 7 AM
to 7 AM. Their reported date may refer to a period ending then, not a local
midnight-to-midnight model interval. Even when a \`t\` code is present, the
source's timezone and effective daily window must be established before a
forecast-observation join.

The system therefore exposes:
- actual station reports and their reported observation dates,
- the earliest usable archived **pre-event model capture**, and
- a verification-status reason.

It **does not** compute actual storm-total snowfall, forecast error,
model winner, or calibrated accuracy without verified interval alignment.
In published JSON, \`forecast_error_in\`, \`actual_observed_storm_total_in\`
and \`model_skill\` deliberately remain \`null\`.

The historical GHCN station clock audit from
\`data/observation_clocks/report.json\` already documents incomplete clocks.
This new station feed is additive; it does not rewrite or relabel older training.

## Additional requirements before automatic scoring

1. Confirm the exact reporting time, timezone, and covered interval at
   each source station, on each relevant date.
2. Align model hourly snowfall from a **frozen forecast issued before the
   event** to precisely those same intervals. A newer forecast capture
   must not overwrite the earlier model's prediction.
3. Compare each station to a spatially representative forecast grid point,
   and record the separation distance.
4. Check for gaps, trace amounts, accumulated/multiday reports, preliminary
   station entries, precipitation-type conflicts, and observer corrections.
5. Hold out entire storms for validation instead of treating neighboring
   stations as independent forecast events.

Only then should a real station-level error or storm-level forecast skill
metric be published.

## Service availability

The observation provider is external. If it is unavailable, the pipeline
publishes a clear status and never falls back to **modeled** weather as
ground truth. A previous successful observation capture can be retained
with its earlier retrieval time, and consumers must label it as cached.

Run locally (requires network for live data):

\`\`\`bash
python -m unittest discover -s tests -p 'test_event_verification.py' -v
python scripts/event_verification.py
\`\`\`
