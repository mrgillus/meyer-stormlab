# StormLab regional event intelligence

## Purpose

Convert existing deterministic daily weather-model forecast captures into persistent, identifiable, revisitable **meteorological pattern signals**. This is a research/forecast-tracking feature, not a warning service, official risk product, or a source of observations.

### Data pipeline

1. The existing \`scripts/collect.py\` writes periodic stitched Open-Meteo forecast captures to \`data/history.json\` (latest 28) and the original per-capture snapshot archive. It currently retrieves GFS, ECMWF, GEM and ICON for eight locations in the Greater St. Louis area.
2. \`scripts/event_watch.py\` independently reads these captures (no network access) and screens daily weather fields, groups neighboring dates by phenomenon, then tries to associate each new signal with an earlier case of the same type if its forecast dates overlap or move no more than two calendar days.
3. \`data/events/catalog.json\` holds persistent cases with one timeline entry per source-capture timestamp, plus first/last detection. Already-processed capture timestamps are recorded so repeated runs do not duplicate historical records.
4. \`data/events/index.json\` offers compact, current signals (including faded and passed-forecast-window cases) for the StormLab frontend.
5. The independent \`.github/workflows/event-watch.yml\` job tests and rebuilds the case data after the existing scheduled collector, four times daily. It only commits files under \`data/events/\` and never modifies the archive or experimental forecast models.

### Screening rules (subject to future calibration)

- Snowfall potential: daily modeled snowfall **at least 2.0 inches**.
- Heavy precipitation: daily modeled precipitation **at least 1.25 inches liquid equivalent**. **This is not a flood forecast and is not necessarily rain.**
- Strong winds: daily modeled maximum gust **at least 45 mph**.
- Unusual cold: daily modeled minimum temperature **10°F or less**.
- Extreme heat: daily modeled maximum temperature **100°F or more**.

The four forecast sources can have **different maximum forecast days**; the longest current collection is roughly 15 days, not 21. The presence of a threshold crossing is a screening signal, **not a calibrated likelihood of an event**. A supporting-model count measures agreement **only when those models flagged the same place and calendar day**, not a probability. Do not equate a large list of supporting locations with independent sources.

### Lifecycle and caution

- \`in_latest_capture\`: at least one triggering daily model projection was found in the latest archived capture.
- \`not_in_latest_capture\`: earlier source guidance suggested the event, but the newest capture did not cross the screening thresholds.
- \`forecast_window_passed\`: forecast dates are behind the current local date. **Does not mean the event occurred.**
- \`first_seen\`, \`last_seen\` and timeline timestamps are **data retrieval times**, not model initialization times.
- Wentzville triggering values in an event record are **only threshold-crossing daily samples**, not storm-total forecasts, a calibrated blend, or ground measurements.
- **Observations and post-event verification are not yet connected.** No forecast skill verdict should be inferred from any of these records.
- This system **cannot** detect tornado, thunderstorm, or flash-flood risk from daily bulk precipitation and wind values alone. It does not replace NWS alerts.
- Existing Thanksgiving-specific seven-model storm guidance is a **separate data source**. Do not represent this four-model event detector as the seven-model blended forecast.

### Development

Run the standard-library tests:

\`\`\`bash
python -m unittest discover -s tests -p 'test_event_watch.py' -v
\`\`\`

Rebuild from locally archived guidance:

\`\`\`bash
python scripts/event_watch.py
\`\`\`

Frontend integration: \`docs/event-watch.js\` reads the compact index for *Storms* and the catalog for the *Storm Files* timeline. Existing \`docs/app.js\` operates the live hourly and specialized-storm views.

See \`.github/workflows/event-watch.yml\` for the actual schedule, publication and retry rules.

### Named weather events

Each case keeps a permanent sequential name in addition to its original ID. Every retained forecast capture is reassessed against the case window as known at that capture. Positive, clear and unknown evidence are preserved separately; faded events remain visible through their latest anticipated date range, then move to Storm Files. Confirmed support is the percentage of the four configured sources crossing the regional threshold anywhere in the remaining window, **not** a probability or same-place concurrence. A source is clear only if all eight locations and remaining dates have valid below-threshold guidance; missing data is unknown. A zero is plotted only when all four sources are clear; partial support is a lower bound with a dashed possible-support range. Historical backfill uses the retained real captures, never invented forecasts. Names are StormLab research labels, not official storm names or NWS watches.

### All-hazards screening (expanded collection)

The collector requests rain and shower sums, daily WMO weather code and apparent temperature extrema alongside the original fields; an unsupported extended API request falls back to the original fields. Ordinary rain and showers are tracked from 0.01 inch per day. Daily codes can flag drizzle, light snow, freezing rain/drizzle, thunderstorms and fog. Freeze at 32°F or less, apparent heat at 105°F or more and apparent cold at 0°F or less are provisional screening criteria, not official warning levels. The original five rules remain unchanged. Routine events are grouped separately on the Storms page. New classifications begin on subsequent captures; prior event identities and histories remain intact. A daily code only describes the most severe coded condition, so an absence does not rule out a hazard. Separate official SPC tornado/hail/wind/severe outlooks and WPC excessive rainfall forecasts are linked but **not yet ingested**. Individual severe storm modes, ice accumulation, blizzard criteria, flood impacts and drought need dedicated data and validation. No calibrated probabilities are implied.

### Date-driven event grouping

The persistent `event_groups` layer groups overlapping regional forecast date ranges across all weather modes. A parent event has one name, one date range and multiple underlying mode IDs. Each mode retains its existing forecast and verification history. Existing parent identities survive model changes; if windows separate the original primary mode keeps its parent identity. The earliest detected mode supplies the initial name, with notable snowfall preferred for simultaneous detection. Prior names are retained as aliases when groups merge. The grouping is a temporal association, not proof that every mode arises from the same meteorological system. Distinct nonoverlapping windows are separate events. No combined event probability is computed.

### Event itinerary

The compact event tile groups hazard modes by family, then by mode type; repeated episodes use separate date rows. Independent daily model support is derived from the latest actual capture, rather than spreading a whole-window threshold hit over every day. Adjacent days are combined only when model states and capture match. Missing coverage stays unknown. Historical or legacy windows without day-level evidence are labeled as window-level or past forecasts. Model histories and verification remain available per underlying mode. The source currently supplies daily guidance for this feature, not hour-level timing or calibrated probabilities.

### Current versus upcoming events

The Storms landing page splits named events using the Central Time calendar: start date through end date inclusive is the current event window; later start dates belong on the radar; passed windows remain in Storm Files. Current window is not an observation that all weather modes are occurring. Faded or incomplete evidence stays visible and labeled. The local forecast narrative remains a separate location-specific detail below the current named event.
