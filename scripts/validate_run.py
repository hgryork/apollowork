#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

REQUIRED_RAW = ["events", "message_context", "fusion_inputs"]
REQUIRED_ANALYSIS = [
    ("analysis/tables/module_phase_table.csv", ["module", "phase_label", "latency_ms"]),
    ("analysis/tables/message_handoff_detail.csv", ["edge_name", "trace_id", "handoff_ms"]),
    ("analysis/tables/trace_link_table.csv", ["fusion_trace_id", "parent_trace_id", "sensor_kind"]),
    ("analysis/tables/e2e_frame_table.csv", ["fusion_trace_id", "parent_trace_id", "complete_path"]),
    ("analysis/tables/control_usage.csv", ["fusion_trace_id", "control_reuse_count"]),
]


def header(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        return next(reader, [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Apollo perf trace run directory for the skill scripts.")
    parser.add_argument("run_dir", help="Run directory containing raw tables and analysis outputs")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    result = {
        "run_dir": str(run_dir),
        "raw": {},
        "analysis": {},
        "status": "pass",
        "notes": [],
    }

    for raw_name in REQUIRED_RAW:
        raw_dir = run_dir / raw_name
        files = list(raw_dir.glob("*.csv")) if raw_dir.exists() else []
        result["raw"][raw_name] = {
            "exists": raw_dir.exists(),
            "file_count": len(files),
        }
        if not raw_dir.exists() or not files:
            result["status"] = "warn"
            result["notes"].append(f"missing or empty raw table family: {raw_name}")

    for rel_path, required_cols in REQUIRED_ANALYSIS:
        path = run_dir / rel_path
        entry = {"exists": path.exists(), "required_columns_present": False}
        if path.exists():
            cols = header(path)
            entry["columns"] = cols
            entry["required_columns_present"] = all(col in cols for col in required_cols)
            if not entry["required_columns_present"]:
                result["status"] = "warn"
                result["notes"].append(f"analysis table missing expected columns: {rel_path}")
        else:
            result["status"] = "warn"
            result["notes"].append(f"missing analysis table: {rel_path}")
        result["analysis"][rel_path] = entry

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
