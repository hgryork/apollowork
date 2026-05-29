#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from apollo_perf_trace_lib import (
    build_drop_events,
    build_latency_drop_alignment,
    build_latency_timeline,
    build_quality_rows,
    compute_run_start_ns,
    copy_csv,
    load_required_tables,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build canonical Apollo perf trace tables and drop-latency alignment outputs.")
    parser.add_argument("run_dir", help="Run directory containing analysis outputs")
    parser.add_argument("--out-dir", help="Output directory for canonical tables; defaults to <run_dir>/analysis/canonical")
    parser.add_argument("--bin-seconds", type=int, default=1, help="Time-bin width in seconds for timeline tables")
    parser.add_argument("--soft-reuse-threshold", type=int, default=4, help="Reuse count threshold for soft-drop candidates")
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

    drop_rows = build_drop_events(run_dir, handoff_rows, trace_rows, e2e_rows, control_rows, soft_reuse_threshold=args.soft_reuse_threshold)
    write_csv_rows(out_dir / "drop_event_table.csv", drop_rows, FIELDNAMES_DROP)

    run_start_ns = compute_run_start_ns(module_rows, handoff_rows, e2e_rows, control_rows)
    timeline_rows = build_latency_timeline(module_rows, handoff_rows, e2e_rows, control_rows, run_start_ns, args.bin_seconds)
    write_csv_rows(out_dir / "latency_timeline_table.csv", timeline_rows, FIELDNAMES_TIMELINE)

    alignment_rows = build_latency_drop_alignment(timeline_rows, drop_rows, run_start_ns, args.bin_seconds)
    write_csv_rows(out_dir / "latency_drop_alignment_table.csv", alignment_rows, FIELDNAMES_ALIGNMENT)

    summary = {
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "bin_seconds": args.bin_seconds,
        "soft_reuse_threshold": args.soft_reuse_threshold,
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
        },
    }
    (out_dir / "build_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
