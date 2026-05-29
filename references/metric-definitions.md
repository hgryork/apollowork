# Metric Definitions

## Core runtime formulas

Use `mono_ns` for runtime latency.

- `phase_latency_ms = (end_ns - start_ns) / 1e6`
- `module_total_ms = (output_pub_ns - proc_enter_ns) / 1e6`
- `handoff_ms = (mono_ns_dst_in - mono_ns_src_out) / 1e6`
- `sensor_to_fusion_ms = (fusion_output_ns - sensor_origin_ns) / 1e6`
- `reaction_time_ms = (first_control_consume_ns - sensor_origin_ns) / 1e6`
- `first_output_latency_ms = (first_control_output_ns - sensor_origin_ns) / 1e6`
- `data_age_ms = (last_control_output_ns - sensor_origin_ns) / 1e6`
- `reuse_tail_ms = (last_control_output_ns - first_control_output_ns) / 1e6`

## Deadline and miss rate

Every deadline must be expressed as:

- object
- start and end anchors
- threshold

Examples:

- `planning_total_deadline = proc_enter -> output_pub < 80ms`
- `planning_to_control_deadline = planning_out -> control_in < 15ms`
- `e2e_rt_deadline = sensor_origin -> first_control_consume < 150ms`

Miss rate:

- `miss_rate = miss_count / eligible_count`
- For charts, prefer `miss_rate_pct = 100 * miss_count / eligible_count`

Do not mix missing-path samples into strict miss rate without stating that explicitly.

## Jitter

This skill supports two practical jitter definitions for timeline plots:

- `std`: standard deviation of sample latency within a time bin
- `jitter`: `p95 - p50` within a time bin

Use `jitter = p95 - p50` when the goal is to show how wide the long tail is without being too sensitive to one extreme sample. Use `std` when the goal is to show general spread.

For module plots, it is often useful to show `p95` together with `jitter`. For example, a planning chart can show that the mean stays stable while tail spread grows.

## Sensor to fusion

Sensor-to-fusion latency is defined as:

- `sensor_to_fusion_ms = (fusion_output_ns - sensor_origin_ns) / 1e6`

This is especially useful for:

- comparing lidar versus radar freshness
- checking whether a parent source is systematically older before fusion
- computing sensor-side miss rate using a freshness or deadline threshold

## Raw versus steady-state

Use raw view when startup behavior matters. Use steady-state view when judging stable runtime performance. If startup instability or collection-boundary truncation is visible, keep raw and steady-state plots separate.

The canonical builder emits `steady_state_summary.csv` with `raw` and `steady` scopes. Its inferred steady start is the first observed complete closed-loop control consumption, unless the caller overrides it with `--steady-start-s`.

## Anomaly rules

Default anomaly rules should be explicit. Typical examples:

- RT anomaly: `reaction_time_ms > p99` or above an absolute threshold.
- Data Age anomaly: `data_age_ms > p99` or above an absolute threshold.
- Planning spike: `planning_total_ms > p99` or above a chosen operational threshold.
- Drop burst window: time bins where `drop_count_total` is unusually high relative to neighboring bins or full-run baseline.

Always show counts with percentile-based anomaly thresholds.

For drop rules, high `control_reuse_count` is not sufficient evidence for `soft_drop`. A `soft_drop` needs a newer planning output that was not consumed, was stale-replaced, or was provably delayed while an older trace kept being used.
