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

## Daily scores stay null; event totals are scored without assuming a clock

Single-day forecast errors remain null. Climate stations often observe on a
different 24-hour window (such as 7 AM to 7 AM), and that window has not been
independently verified for these stations.

Snow and liquid-precipitation events are instead scored as **clock-independent
event totals** (`scripts/event_totals.py`, method version 1):

- A report dated D covers (D-1 at clock c, D at clock c]. So reports dated
  S..E+1 always cover the local forecast window [S 00:00, E+1 00:00), for any
  clock 0 < c <= 24h. The overhang is exactly 24 hours, split before S and
  after E+1, and lies inside forecast days S-1 and E+1.
- The window starts from the catalog event days. It widens (up to 2 days per
  side) while the 4-model mean forecast at any town exceeds 0.1" snow
  (0.01" liquid) on an adjacent day. Widening uses only the frozen pre-event
  capture, re-selected so it precedes the widened start. It never looks at
  observations. Events that cannot be bracketed are not scored.
- Each station-model pair records `clock_ambiguity_in` = forecast on S-1 plus
  E+1. It counts toward scores only if that is <= max(0.1", 10% of the
  forecast total) for snow, or max(0.01", 10%) for liquid.
- A station is scored only if every report S..E+1 is measured or trace (trace
  = 0 with a +0.05" upper bound per day). Missing, flagged, accumulated or
  out-of-range reports, or a reported clock of 0, disqualify it. It must lie
  within 10 miles of a forecast town, and it is compared with that town's
  forecast.
- Output per event: `event_total_verification`, with per-pair observed and
  forecast totals, per-model MAE/bias/RMSE, and 1"/4" (snow) or 0.25"/1"
  (liquid) contingency counts. `cases_scored` counts events with such a record.

Limits: one event at correlated, nearby point stations is a record of that
storm, not a skill estimate or calibrated probability. Station points are not
area averages. The legacy fields `forecast_error_in`,
`actual_observed_storm_total_in` and `model_skill` remain null.

## Requirements before daily (sub-event) scoring

Event totals above sidestep requirements 1–2 by construction. Daily or hourly
scores still need all of these:

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
