#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from apollo_perf_trace_lib import (
    build_latency_drop_alignment,
    build_latency_timeline,
    compute_run_start_ns,
    read_csv_rows,
    write_csv_rows,
)

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
    parser = argparse.ArgumentParser(description="Regenerate latency timeline and drop-latency alignment from canonical tables.")
    parser.add_argument("canonical_dir", help="Directory containing canonical tables")
    parser.add_argument("--bin-seconds", type=int, default=1, help="Time-bin width in seconds")
    args = parser.parse_args()

    canonical_dir = Path(args.canonical_dir).resolve()
    module_rows = read_csv_rows(canonical_dir / "module_phase_table.csv")
    handoff_rows = read_csv_rows(canonical_dir / "message_handoff_table.csv")
    e2e_rows = read_csv_rows(canonical_dir / "e2e_frame_table.csv")
    control_rows = read_csv_rows(canonical_dir / "control_usage_table.csv")
    drop_rows = read_csv_rows(canonical_dir / "drop_event_table.csv")

    run_start_ns = compute_run_start_ns(module_rows, handoff_rows, e2e_rows, control_rows)
    timeline_rows = build_latency_timeline(module_rows, handoff_rows, e2e_rows, control_rows, run_start_ns, args.bin_seconds)
    alignment_rows = build_latency_drop_alignment(timeline_rows, drop_rows, run_start_ns, args.bin_seconds)

    write_csv_rows(canonical_dir / "latency_timeline_table.csv", timeline_rows, FIELDNAMES_TIMELINE)
    write_csv_rows(canonical_dir / "latency_drop_alignment_table.csv", alignment_rows, FIELDNAMES_ALIGNMENT)

    result = {
        "canonical_dir": str(canonical_dir),
        "bin_seconds": args.bin_seconds,
        "timeline_rows": len(timeline_rows),
        "alignment_rows": len(alignment_rows),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
