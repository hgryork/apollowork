# Table Contracts

This reference defines what each canonical table row means and what fields must not be ambiguous.

## module_phase_table

- One row = one phase pair sample.
- Required identity: `module`, `phase_label`, `trace_id` or `proc_id`, `start_ns`, `end_ns`.
- Must retain `pair_method`.

## message_handoff_table

- One row = one attempted upstream-out to downstream-in edge match.
- Must keep both matched and unmatched rows.
- Required fields: `edge_name`, `trace_id`, `mono_ns_src`, `mono_ns_dst`, `matched`, `unmatched_reason`.

## trace_link_table

- One row = one `parent_trace_id -> fusion_trace_id` relationship.
- Required fields: `sensor_kind`, `sensor_origin_ns`, `fusion_output_ns`, `sensor_to_fusion_ms`.
- If origin is missing, keep the row and mark the missing state.

## e2e_frame_table

- One row = one `(parent_trace_id, fusion_trace_id)` lifecycle.
- Do not collapse all parents into one fusion-only row.
- Required fields: `reaction_time_ms`, `first_output_latency_ms`, `data_age_ms`, `complete_path`, `missing_stage`.

## control_usage_table

- One row = one planning/fusion frame's control-consumption summary.
- Required fields: `first_control_consume_ns`, `first_control_output_ns`, `last_control_output_ns`, `control_reuse_count`, `reuse_tail_ms`.

## quality_table

- One row = one quality metric.
- Required fields: `metric_name`, `metric_value`, `threshold`, `status`.

## drop_event_table

- One row = one distinct drop event.
- Required fields: `drop_type`, `break_stage`, `break_ns`, `gap_ms`, `missed_period_count`, `evidence_type`, `evidence_detail`.
- Must preserve event-level evidence, not only aggregate counts.

## latency_timeline_table

- One row = one time bin.
- Required fields: `time_bin_s`, counts, RT/Data Age/Planning summary statistics.

## latency_drop_alignment_table

- One row = one aligned time bin.
- Required fields: `time_bin_s`, `drop_count_total`, `rt_p95`, `data_age_p95`, `planning_total_p95`, `planning_wait_p95`, `reuse_p95`.
- Use this table whenever reasoning about drop-latency correlation.
