#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from apollo_perf_trace_lib import (
    build_anomaly_frame_table,
    build_deadline_metrics,
    build_drop_events,
    build_latency_drop_alignment,
    build_latency_timeline,
    build_quality_rows,
    build_steady_state_summary,
    choose_anchor_ns,
    compute_run_start_ns,
    copy_csv,
    infer_deadline_config,
    load_required_tables,
    to_int,
    write_csv_rows,
)

FIELDNAMES_QUALITY = ["metric_name", "metric_value", "threshold", "status", "notes"]
FIELDNAMES_DROP = [
    "drop_event_id",
    "drop_type",
    "break_stage",
    "anchor_trace_id",
    "parent_trace_id",
    "break_ns",
    "last_good_ns",
    "resume_ns",
    "gap_ms",
    "expected_period_ms",
    "missed_period_count",
    "evidence_type",
    "evidence_detail",
]
FIELDNAMES_TIMELINE = [
    "time_bin_s",
    "sample_count",
    "complete_count",
    "rt_p50",
    "rt_p95",
    "rt_p99",
    "data_age_p50",
    "data_age_p95",
    "data_age_p99",
    "planning_total_p50",
    "planning_total_p95",
    "planning_wait_p95",
    "reuse_p95",
]
FIELDNAMES_ALIGNMENT = [
    "time_bin_s",
    "drop_count_total",
    "drop_count_by_stage",
    "rt_p95",
    "data_age_p95",
    "planning_total_p95",
    "planning_wait_p95",
    "reuse_p95",
    "corr_tag",
    "lead_lag_tag",
]
FIELDNAMES_STEADY = [
    "scope",
    "steady_start_s",
    "sample_count",
    "complete_count",
    "drop_count_total",
    "rt_p50",
    "rt_p95",
    "rt_p99",
    "rt_max",
    "data_age_p50",
    "data_age_p95",
    "data_age_p99",
    "data_age_max",
    "planning_total_p95",
    "planning_total_p99",
    "planning_wait_p95",
    "planning_wait_p99",
    "reuse_p95",
    "reuse_p99",
]
FIELDNAMES_DEADLINE = [
    "scope",
    "metric_name",
    "object",
    "start_anchor",
    "end_anchor",
    "threshold_ms",
    "threshold_source",
    "base_period_ms",
    "eligible_count",
    "miss_count",
    "miss_rate_pct",
]
FIELDNAMES_ANOMALY = [
    "fusion_trace_id",
    "sensor_kind",
    "anchor_ns",
    "relative_s",
    "is_steady",
    "reaction_time_ms",
    "data_age_ms",
    "rt_threshold_ms",
    "data_age_threshold_ms",
    "anomaly_type",
    "severity_score",
    "root_cause_tag",
    "planning_total_ms",
    "planning_wait_ms",
    "reuse_tail_ms",
    "control_reuse_count",
    "complete_path",
    "evidence",
]


def infer_steady_start_s(e2e_rows, run_start_ns: int):
    all_anchors = [
        choose_anchor_ns(row, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns"))
        for row in e2e_rows
    ]
    complete_anchors = [
        choose_anchor_ns(row, ("first_control_consume_ns", "first_control_output_ns", "last_control_output_ns"))
        for row in e2e_rows
        if to_int(row.get("complete_path"), 0) == 1
    ]
    all_anchors = [v for v in all_anchors if v]
    complete_anchors = [v for v in complete_anchors if v]
    if not all_anchors or not complete_anchors:
        return None
    return max((min(complete_anchors) - run_start_ns) / 1e9, 0.0)


def load_deadline_overrides(args):
    overrides = {}
    if args.deadline_config:
        path = Path(args.deadline_config).resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        aliases = {
            "planning_total_deadline_ms": "planning_total_deadline",
            "planning_to_control_deadline_ms": "planning_to_control_deadline",
            "e2e_rt_deadline_ms": "e2e_rt_deadline",
        }
        for key, value in data.items():
            metric_name = aliases.get(key, key)
            overrides[metric_name] = float(value)
    direct = {
        "planning_total_deadline": args.planning_total_deadline_ms,
        "planning_to_control_deadline": args.planning_to_control_deadline_ms,
        "e2e_rt_deadline": args.e2e_rt_deadline_ms,
    }
    for key, value in direct.items():
        if value is not None:
            overrides[key] = float(value)
    return overrides


def main() -> int:
    parser = argparse.ArgumentParser(description="Build canonical Apollo perf trace tables and drop-latency alignment outputs.")
    parser.add_argument("run_dir", help="Run directory containing analysis outputs")
    parser.add_argument("--out-dir", help="Output directory for canonical tables; defaults to <run_dir>/analysis/canonical")
    parser.add_argument("--bin-seconds", type=int, default=1, help="Time-bin width in seconds for timeline tables")
    parser.add_argument("--soft-reuse-threshold", type=int, default=4, help="Reuse count threshold for soft-drop candidates")
    parser.add_argument("--stale-grace-periods", type=int, default=1, help="Control periods allowed before a newer planning output is treated as stale")
    parser.add_argument("--steady-start-s", type=float, help="Override inferred steady-state start in seconds from run start")
    parser.add_argument("--top-anomaly-frames", type=int, default=50, help="Maximum rows in anomaly_frame_table.csv")
    parser.add_argument("--deadline-config", help="Optional JSON deadline overrides by metric name, in milliseconds")
    parser.add_argument("--planning-total-deadline-ms", type=float, help="Override planning total deadline in milliseconds")
    parser.add_argument("--planning-to-control-deadline-ms", type=float, help="Override planning to control deadline in milliseconds")
    parser.add_argument("--e2e-rt-deadline-ms", type=float, help="Override E2E reaction-time deadline in milliseconds")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else run_dir / "analysis" / "canonical"
    out_dir.mkdir(parents=True, exist_ok=True)

    tables = load_required_tables(run_dir)
    module_rows = tables["module_phase"]
    handoff_rows = tables["handoff"]
    trace_rows = tables["trace_link"]
    e2e_rows = tables["e2e"]
    control_rows = tables["control_usage"]

    mapping = {
        run_dir / "analysis" / "tables" / "module_phase_table.csv": out_dir / "module_phase_table.csv",
        run_dir / "analysis" / "tables" / "message_handoff_detail.csv": out_dir / "message_handoff_table.csv",
        run_dir / "analysis" / "tables" / "trace_link_table.csv": out_dir / "trace_link_table.csv",
        run_dir / "analysis" / "tables" / "e2e_frame_table.csv": out_dir / "e2e_frame_table.csv",
        run_dir / "analysis" / "tables" / "control_usage.csv": out_dir / "control_usage_table.csv",
    }
    for src, dst in mapping.items():
        copy_csv(src, dst)

    quality_rows = build_quality_rows(run_dir, module_rows, handoff_rows, trace_rows, e2e_rows)
    write_csv_rows(out_dir / "quality_table.csv", quality_rows, FIELDNAMES_QUALITY)

    run_start_ns = compute_run_start_ns(module_rows, handoff_rows, e2e_rows, control_rows)
    steady_start_s = args.steady_start_s
    if steady_start_s is None:
        steady_start_s = infer_steady_start_s(e2e_rows, run_start_ns)
    deadline_config = infer_deadline_config(module_rows, e2e_rows, load_deadline_overrides(args))

    drop_rows = build_drop_events(
        run_dir,
        handoff_rows,
        trace_rows,
        e2e_rows,
        control_rows,
        soft_reuse_threshold=args.soft_reuse_threshold,
        stale_grace_periods=args.stale_grace_periods,
    )
    write_csv_rows(out_dir / "drop_event_table.csv", drop_rows, FIELDNAMES_DROP)

    timeline_rows = build_latency_timeline(module_rows, handoff_rows, e2e_rows, control_rows, run_start_ns, args.bin_seconds)
    write_csv_rows(out_dir / "latency_timeline_table.csv", timeline_rows, FIELDNAMES_TIMELINE)

    alignment_rows = build_latency_drop_alignment(timeline_rows, drop_rows, run_start_ns, args.bin_seconds)
    write_csv_rows(out_dir / "latency_drop_alignment_table.csv", alignment_rows, FIELDNAMES_ALIGNMENT)

    steady_rows = build_steady_state_summary(
        module_rows,
        handoff_rows,
        e2e_rows,
        control_rows,
        drop_rows,
        run_start_ns,
        steady_start_s,
    )
    write_csv_rows(out_dir / "steady_state_summary.csv", steady_rows, FIELDNAMES_STEADY)

    deadline_rows = build_deadline_metrics(module_rows, handoff_rows, e2e_rows, run_start_ns, steady_start_s, deadline_config)
    write_csv_rows(out_dir / "deadline_metrics.csv", deadline_rows, FIELDNAMES_DEADLINE)

    anomaly_rows = build_anomaly_frame_table(
        module_rows,
        handoff_rows,
        e2e_rows,
        control_rows,
        run_start_ns,
        steady_start_s,
        args.top_anomaly_frames,
    )
    write_csv_rows(out_dir / "anomaly_frame_table.csv", anomaly_rows, FIELDNAMES_ANOMALY)

    summary = {
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "bin_seconds": args.bin_seconds,
        "soft_reuse_threshold": args.soft_reuse_threshold,
        "stale_grace_periods": args.stale_grace_periods,
        "steady_start_s": steady_start_s,
        "deadline_config": deadline_config,
        "rows": {
            "module_phase_table": len(module_rows),
            "message_handoff_table": len(handoff_rows),
            "trace_link_table": len(trace_rows),
            "e2e_frame_table": len(e2e_rows),
            "control_usage_table": len(control_rows),
            "quality_table": len(quality_rows),
            "drop_event_table": len(drop_rows),
            "latency_timeline_table": len(timeline_rows),
            "latency_drop_alignment_table": len(alignment_rows),
            "steady_state_summary": len(steady_rows),
            "deadline_metrics": len(deadline_rows),
            "anomaly_frame_table": len(anomaly_rows),
        },
    }
    (out_dir / "build_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
