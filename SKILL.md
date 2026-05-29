---
name: apollo-perf-trace-analysis
description: Use when analyzing Apollo perf trace run directories based on `events`, `message_context`, and `fusion_inputs`, especially for RT/Data Age, planning spikes, handoff delay, control reuse, drop-frame distribution, drop-latency alignment, standard reports, or drill-down by frame, time window, module, anomaly type, or break stage. Also use when the user asks in Chinese for ????????????????????????????????????????
---

# Apollo Perf Trace Analysis

## Overview

Use this skill when the task is to analyze Apollo perf trace data from a run directory, especially when the source of truth is the three raw tables `events`, `message_context`, and `fusion_inputs`. The goal is not only to produce latency summaries, but to build a full-link explanation of module timing, handoff timing, RT, Data Age, control reuse, drop locations, and the relationship between drop bursts and latency spikes.

This skill is designed for interactive analysis. It should support both full-run overview mode and drill-down mode. If the user asks a vague question, start with overview mode and then recommend the next most useful drill-down. If the user asks for a specific frame, time window, module, anomaly set, or drop stage, switch directly to that mode.

## When To Use

Use this skill when one or more of the following are true:

- The user wants Apollo full-link latency analysis based on perf trace output.
- The user mentions `events`, `message_context`, `fusion_inputs`, `analysis/tables`, or `perf_trace`.
- The user asks about RT, Data Age, planning spikes, handoff delay, control reuse, or radar versus lidar timing.
- The user wants anomaly attribution, drop-frame location distribution, or drop-latency alignment.
- The user wants a standard report or an SOP-consistent analysis result.
- The user asks in Chinese for `??`, `????`, `???`, `?????`, `?????`, or `????`.

Do not use this skill for generic system profiling, CPU flame graphs, kernel scheduling analysis, or debugging unrelated code paths unless the analysis still centers on Apollo perf trace tables.

## Required Inputs

Prefer a run directory that contains these raw sources:

- `events/`
- `message_context/`
- `fusion_inputs/`

If derived analysis tables already exist, they may be used to accelerate the analysis, but the skill should still treat the three raw tables as the source of truth. When the skill relies on already-derived tables instead of rebuilding them, it must say so explicitly.

Before drawing conclusions, verify the data source state:

1. Are all three raw table families present?
2. Are shard files non-empty and structurally readable?
3. Is there enough trace coverage and complete-path coverage to support E2E conclusions?

If one or more raw inputs are missing, downgrade the analysis scope and say exactly which parts are no longer trustworthy.

## Interaction Modes

This skill supports six primary interaction modes. The assistant should choose the mode from the user's request and keep the response shaped to that mode instead of always forcing a full report.

### 1. Overview Mode

Use when the user asks for a run summary, a first pass, or a general health check.

Typical requests:

- `???? run`
- `???? RT ? Data Age ??`
- `???????????`
- `planning ???????`

Default output:

- Data quality conclusion
- Global RT/Data Age summary
- Module and handoff summary
- Top anomaly counts
- Drop location distribution summary
- Drop-latency alignment summary
- Suggested next drill-down

### 2. Time Window Mode

Use when the user asks for a specific time range or segment.

Typical requests:

- `? 25 ? 40 ?`
- `?? 133 ? 147 ????`
- `?? 50-71 ??????`
- `? 100 ????????`

Default output:

- Window-level RT/Data Age statistics
- Planning and handoff statistics in that window
- Drop count and break-stage distribution in that window
- Top anomaly frames in that window
- Comparison against steady-state or full-run baseline
- Local chart suggestions or generated charts if available

### 3. Frame Mode

Use when the user asks for one frame, one trace, or the worst sample.

Typical requests:

- `? fusion_trace_id=...`
- `??????? RT ?`
- `? Data Age ??????`
- `??????????`

Default output:

- Full time ledger from sensor origin to control outputs
- Segment-by-segment contribution breakdown
- Planning total / planner / runonce / wait / reuse details
- Relative severity versus steady-state distribution
- Attribution label and justification
- Related drops in the same time window if any

### 4. Module Or Edge Mode

Use when the user focuses on a module or a handoff edge.

Typical requests:

- `?? planning`
- `? control ????`
- `? prediction ? planning ???`
- `? lidar_detection ??`

Default output:

- Module total and sub-phase statistics or edge handoff statistics
- Timeline behavior and hotspot windows
- Top slow samples
- Overlap with anomaly frames or drops
- Whether the issue is compute-dominant, wait-dominant, or data-quality-dominant

### 5. Anomaly Mode

Use when the user wants abnormal samples or long-tail behavior.

Typical requests:

- `?? RT ??`
- `? Data Age ??`
- `? planning compute spike ???`
- `?? reuse ??`

Default output:

- Rule used to identify anomalies
- Severity bands and sample counts
- Top representative samples
- Clustered attribution patterns
- Recommended next drill-down path

### 6. Drop Mode

Use when the user asks specifically about drops, misses, or chain breaks.

Typical requests:

- `??????????`
- `? planning_to_control ? drop`
- `? drop ? RT ??????`
- `???????? drop ??????`

Default output:

- Drop type and break-stage distribution
- Gap and missed-period summaries
- Drop timeline
- Alignment with RT/Data Age/Planning timelines
- Whether drops and latency spikes are synchronized, lead-lag related, or mostly independent

## Standard Workflow

Follow this workflow unless the user clearly requests a narrower drill-down:

1. Validate the run directory and raw tables.
2. Decide whether to load existing derived tables or rebuild the canonical view from raw data.
3. Apply the data quality gate before making strong claims.
4. Separate raw view and steady-state view when startup or unstable windows matter.
5. Choose the interaction mode from the user's request.
6. Use the minimal set of derived tables needed for that mode.
7. Always ground conclusions in numeric evidence.
8. If the task involves drops, align drop timeline and latency timeline instead of analyzing them in isolation.
9. If the user requests a report, use the report template reference and keep the narrative evidence-first.

## Scripts

Prefer the bundled scripts when the task is to standardize outputs instead of only narrating an analysis:

- `scripts/validate_run.py <run_dir>`
  Use first when you need to verify that the run directory and required analysis tables are present and structurally usable.

- `scripts/build_canonical_tables.py <run_dir>`
  Use when the user wants canonical outputs for this skill. In `v1`, this script expects the standard analyzer outputs under `analysis/tables` and then produces skill-ready canonical tables, including `quality_table.csv`, `drop_event_table.csv`, `latency_timeline_table.csv`, `latency_drop_alignment_table.csv`, `steady_state_summary.csv`, `deadline_metrics.csv`, and `anomaly_frame_table.csv`.

- `scripts/align_drop_latency.py <canonical_dir>`
  Use when canonical tables already exist and the task is to recompute time-bin views or drop-latency alignment without rebuilding everything else.

- `scripts/plot_canonical_metrics.py <canonical_dir> --metrics ... [--start-s ... --end-s ...]`
  Use when the user asks for a chart in natural language, especially for a specific time window. This script renders a zero-dependency SVG chart from canonical timeline tables, so plot requests do not depend on `matplotlib` being installed.

- `scripts/plot_trace_suite.py <canonical_dir> ...`
  Use when the user asks for richer chart types. This script supports:
  - timeline line charts for RT / Data Age / Planning / drop-alignment metrics
  - dual-axis charts such as `rt_p95` versus `drop_count_total`
  - module execution charts with p95, jitter, std, and miss rate
  - sensor→fusion charts by `sensor_kind` or `sensor`
  - stacked drop-count charts by `break_stage`

When scripts are used, report the output directory and any important assumptions, especially that the current `v1` canonical builder normalizes existing analyzer outputs and enriches them with drop and alignment tables.

## Analysis Guardrails

Do not skip these rules:

- Never compute module timing from `data_ts_ns`; use `mono_ns` for runtime latency.
- Never quote RT or Data Age without stating the time anchors.
- Never merge incomplete-path samples into strict E2E statistics without calling that out.
- Never describe a frame as a drop without naming the drop type and break stage.
- Never treat high control reuse alone as `soft_drop`; require evidence that a newer planning output was not consumed or was stale-replaced.
- Never describe correlation between drops and latency spikes without showing aligned time windows.
- Never rely on one extreme sample if the window sample count is too small; always report counts.
- Never collapse all sensors into one row when sensor-origin differences matter.
- Never give only a plot without a sentence explaining what the plot proves.

## Output Contract

When responding with analysis, the skill should prefer a compact but evidence-rich structure:

1. State the scope: full run, time window, frame, module, anomaly set, or drop set.
2. State the data source: raw tables, derived tables, or both.
3. Report the exact metrics and counts used.
4. Explain the attribution or the uncertainty.
5. If helpful, suggest one or two high-value next drill-downs.

If files are generated, provide absolute paths. If a chart cannot be rendered, specify exactly which plot should be inserted and what axes or overlays it should contain.

## References

Load references selectively:

- Read `references/sop.md` when you need the full canonical workflow or the formal table system.
- Read `references/table-contracts.md` when deriving or validating intermediate tables.
- Read `references/metric-definitions.md` when computing RT, Data Age, deadlines, miss rate, or anomaly thresholds.
- Read `references/interaction-modes.md` when shaping a response to a specific user request pattern.
- Read `references/example-prompts.md` when you want concrete examples of how teammates can ask for overviews, drill-downs, or charts in natural language.
- Read `references/report-template.md` when writing a full analysis report or a presentation-style summary.

Do not load every reference by default. Keep the context lean and pull in only what the current task needs.
