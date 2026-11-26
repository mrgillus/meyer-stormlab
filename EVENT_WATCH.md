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

Frontend integration: \`docs/event-watch.js\` reads the compact index for *Horizon* and the catalog for the *Storm Files* timeline. Existing \`docs/app.js\` operates the live hourly and specialized-storm views.

See \`.github/workflows/event-watch.yml\` for the actual schedule, publication and retry rules.
