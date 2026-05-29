# Interaction Modes

Use this reference when the user has already made it clear what they want to inspect. The goal is to choose the smallest useful response mode and avoid forcing a full-report structure every time.

## Time window requests

Recognize requests like:

- `? 25 ? 40 ?`
- `?? 133-147s`
- `? 100 ???`
- `?????????`

Default response contract:

- Quote the exact time window used.
- State sample count in that window.
- Report RT/Data Age/Planning summary in that window.
- Report drops in that window.
- Name the top anomaly frames in that window.
- Compare against steady-state or global baseline.

## Frame requests

Recognize requests like:

- `???`
- `?? fusion_trace_id=...`
- `??????`
- `??????`

Default response contract:

- Show the full ledger from sensor origin to control.
- Break the total into segment contributions.
- State planning total, planner, runonce, wait, reuse.
- State whether nearby drops exist.
- Give one attribution label and defend it with numbers.

## Module or edge requests

Recognize requests like:

- `? planning`
- `?? control`
- `? prediction ? planning ???`

Default response contract:

- Give module or edge summary statistics.
- Highlight hotspot windows and top slow samples.
- State whether the issue is compute, wait, or data freshness related.
- Mention overlap with anomalies or drops if relevant.

## Anomaly requests

Recognize requests like:

- `?? RT ??`
- `? Data Age ??`
- `? planning spike`

Default response contract:

- State the anomaly rule.
- Give counts by severity.
- Give representative samples.
- Give grouped attribution patterns.

## Drop requests

Recognize requests like:

- `? drop`
- `? planning_to_control ? drop`
- `drop ? RT ?????`

Default response contract:

- State drop type and break-stage scope.
- Give counts, gap, and missed periods.
- Align drop timeline with latency timeline.
- State whether they are synchronized, lead-lag related, or mostly independent.
