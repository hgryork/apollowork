#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

EDGE_DEFAULT_PERIOD_MS = {
    "perception_to_prediction": 50.0,
    "prediction_to_planning": 200.0,
    "planning_to_control": 100.0,
    "fusion_to_prediction": 50.0,
    "sensor_to_fusion": 80.0,
    "planning_internal": 80.0,
    "control_consume_or_reuse": 10.0,
}

INCOMPLETE_STAGE_TO_BREAK = {
    "sensor_origin": ("parent_missing", "sensor_to_fusion"),
    "prediction_in": ("hard_drop", "perception_to_prediction"),
    "prediction_out": ("hard_drop", "prediction_internal"),
    "planning_in": ("hard_drop", "prediction_to_planning"),
    "planning_out": ("hard_drop", "planning_internal"),
    "first_control_consume": ("hard_drop", "planning_to_control"),
    "first_control_output": ("soft_drop", "control_consume_or_reuse"),
    "control_consume": ("hard_drop", "planning_to_control"),
    "control_first_out": ("soft_drop", "control_consume_or_reuse"),
    "control_last_out": ("soft_drop", "control_consume_or_reuse"),
}

FALLBACK_DEADLINES_MS = {
    "planning_total_deadline": 80.0,
    "planning_to_control_deadline": 15.0,
    "e2e_rt_deadline": 150.0,
    "e2e_data_age_deadline": 240.0,
}

def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: Path, rows: Sequence[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _stringify(row.get(k, "")) for k in fieldnames})


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text else "0"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def copy_csv(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def to_int(value: object, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def to_float(value: object, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def nonzero_int(value: object) -> Optional[int]:
    parsed = to_int(value, 0)
    return parsed if parsed > 0 else None


def avg(values: Sequence[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * (p / 100.0)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return clean[lower]
    weight = rank - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def choose_anchor_ns(row: Dict[str, str], keys: Sequence[str]) -> Optional[int]:
    for key in keys:
        value = nonzero_int(row.get(key))
        if value:
            return value
    return None


def bin_seconds_for_ns(ns: int, run_start_ns: int, bin_seconds: int) -> int:
    return int(((ns - run_start_ns) / 1e9) // bin_seconds) * bin_seconds


def compute_run_start_ns(
    module_rows: Sequence[Dict[str, str]],
    handoff_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
    control_rows: Sequence[Dict[str, str]],
) -> int:
    candidates: List[int] = []
    for row in module_rows:
        for key in ("enter_ns", "exit_ns"):
            v = nonzero_int(row.get(key))
            if v:
                candidates.append(v)
    for row in handoff_rows:
        for key in ("mono_ns_src", "mono_ns_dst"):
            v = nonzero_int(row.get(key))
            if v:
                candidates.append(v)
    for row in e2e_rows:
        for key in (
            "sensor_origin_ns",
            "fusion_input_ns",
            "fusion_output_ns",
            "prediction_input_ns",
            "prediction_output_ns",
            "planning_input_ns",
            "planning_output_ns",
            "first_control_consume_ns",
            "first_control_output_ns",
            "last_control_output_ns",
        ):
            v = nonzero_int(row.get(key))
            if v:
                candidates.append(v)
    for row in control_rows:
        for key in ("first_control_consume_ns", "first_control_output_ns", "last_control_output_ns"):
            v = nonzero_int(row.get(key))
            if v:
                candidates.append(v)
    if not candidates:
        raise RuntimeError("Unable to determine run start time from analysis tables.")
    return min(candidates)


def median_period_ms(values_ns: Sequence[int]) -> Optional[float]:
    sorted_vals = sorted(v for v in values_ns if v is not None)
    if len(sorted_vals) < 3:
        return None
    diffs = [(b - a) / 1e6 for a, b in zip(sorted_vals, sorted_vals[1:]) if b > a]
    if not diffs:
        return None
    return float(median(diffs))


def infer_deadline_config(
    module_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
    overrides: Optional[Dict[str, float]] = None,
) -> Dict[str, Dict[str, object]]:
    overrides = overrides or {}
    planning_enter_times = sorted({
        nonzero_int(row.get("enter_ns"))
        for row in module_rows
        if row.get("module") == "planning" and row.get("phase_label") == "total"
    } - {None})
    e2e_anchor_times = sorted({
        choose_anchor_ns(row, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns"))
        for row in e2e_rows
        if to_int(row.get("complete_path"), 0) == 1
    } - {None})
    planning_period_ms = median_period_ms(planning_enter_times)
    e2e_period_ms = median_period_ms(e2e_anchor_times)
    base_period_ms = planning_period_ms or e2e_period_ms

    if base_period_ms:
        inferred = {
            "planning_total_deadline": base_period_ms,
            "planning_to_control_deadline": max(5.0, base_period_ms * 0.20),
            "e2e_rt_deadline": base_period_ms * 2.0,
            "e2e_data_age_deadline": base_period_ms * 3.0,
        }
        source = "inferred_from_planning_period"
    else:
        inferred = dict(FALLBACK_DEADLINES_MS)
        source = "fallback_default"

    result: Dict[str, Dict[str, object]] = {}
    for metric_name, inferred_threshold in inferred.items():
        if metric_name in overrides:
            result[metric_name] = {
                "threshold_ms": overrides[metric_name],
                "threshold_source": "override",
                "base_period_ms": base_period_ms or "",
            }
        else:
            result[metric_name] = {
                "threshold_ms": inferred_threshold,
                "threshold_source": source,
                "base_period_ms": base_period_ms or "",
            }
    return result


def nearest_gap_context(reference_times_ns: Sequence[int], current_ns: int) -> Tuple[Optional[int], Optional[int], Optional[float]]:
    prev_ns = None
    next_ns = None
    for t in reference_times_ns:
        if t < current_ns:
            prev_ns = t
        elif t > current_ns:
            next_ns = t
            break
    gap_ms = ((next_ns - prev_ns) / 1e6) if prev_ns and next_ns and next_ns > prev_ns else None
    return prev_ns, next_ns, gap_ms


def relative_s(ns: Optional[int], run_start_ns: int) -> Optional[float]:
    if not ns:
        return None
    return (ns - run_start_ns) / 1e9


def is_at_or_after_s(ns: Optional[int], run_start_ns: int, start_s: Optional[float]) -> bool:
    if start_s is None:
        return True
    rel = relative_s(ns, run_start_ns)
    return rel is not None and rel >= start_s


def choose_best_e2e_row(rows: Sequence[Dict[str, str]]) -> Dict[str, str]:
    def score(row: Dict[str, str]) -> Tuple[int, int, int, int]:
        anchors = sum(1 for key in (
            "sensor_origin_ns",
            "fusion_output_ns",
            "prediction_input_ns",
            "prediction_output_ns",
            "planning_input_ns",
            "planning_output_ns",
            "first_control_consume_ns",
            "first_control_output_ns",
            "last_control_output_ns",
        ) if nonzero_int(row.get(key)))
        sensor_rank = 1 if (row.get("sensor_kind") or "").lower() == "lidar" else 0
        rt = to_float(row.get("reaction_time_ms"), 0.0) or 0.0
        age = to_float(row.get("data_lifetime_ms")) or to_float(row.get("data_age_ms"), 0.0) or 0.0
        return (to_int(row.get("complete_path"), 0), anchors, int(max(rt, age) * 1000), sensor_rank)

    return max(rows, key=score)


def e2e_rows_by_fusion(e2e_rows: Sequence[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in e2e_rows:
        fusion_trace_id = row.get("fusion_trace_id") or ""
        if fusion_trace_id:
            grouped[fusion_trace_id].append(row)
    return {trace_id: choose_best_e2e_row(rows) for trace_id, rows in grouped.items()}


def control_rows_by_trace(control_rows: Sequence[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    rows: Dict[str, Dict[str, str]] = {}
    for row in control_rows:
        fusion_trace_id = row.get("fusion_trace_id") or ""
        if fusion_trace_id:
            rows[fusion_trace_id] = row
    return rows


def planning_output_rows(e2e_rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    rows = [
        row for row in e2e_rows_by_fusion(e2e_rows).values()
        if nonzero_int(row.get("planning_output_ns"))
    ]
    rows.sort(key=lambda row: nonzero_int(row.get("planning_output_ns")) or 0)
    return rows


def consumed_control_rows(control_rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    rows = [row for row in control_rows if nonzero_int(row.get("first_control_consume_ns"))]
    rows.sort(key=lambda row: nonzero_int(row.get("first_control_consume_ns")) or 0)
    return rows


def find_previous_control_reuse(control_rows: Sequence[Dict[str, str]], ns: int) -> Optional[Dict[str, str]]:
    previous = None
    for row in consumed_control_rows(control_rows):
        first_consume = nonzero_int(row.get("first_control_consume_ns"))
        last_output = nonzero_int(row.get("last_control_output_ns"))
        if first_consume and last_output and first_consume <= ns < last_output:
            previous = row
        elif first_consume and first_consume > ns:
            break
    return previous


def find_next_control_consume(control_rows: Sequence[Dict[str, str]], ns: int) -> Optional[Dict[str, str]]:
    for row in consumed_control_rows(control_rows):
        first_consume = nonzero_int(row.get("first_control_consume_ns"))
        if first_consume and first_consume > ns:
            return row
    return None


def read_event_rows(run_dir: Path, module: str) -> List[Dict[str, str]]:
    events_dir = run_dir / "events"
    if not events_dir.exists():
        return []
    rows: List[Dict[str, str]] = []
    for path in sorted(events_dir.glob(f"{module}*.csv")):
        rows.extend(read_csv_rows(path))
    return rows


def load_required_tables(run_dir: Path) -> Dict[str, List[Dict[str, str]]]:
    tables_dir = run_dir / "analysis" / "tables"
    required = {
        "module_phase": (tables_dir / "module_phase_table.csv",),
        "handoff": (tables_dir / "message_handoff_detail.csv", tables_dir / "message_handoff_table.csv"),
        "trace_link": (tables_dir / "trace_link_table.csv",),
        "e2e": (tables_dir / "e2e_frame_table.csv",),
        "control_usage": (tables_dir / "control_usage.csv", tables_dir / "control_usage_table.csv"),
    }
    resolved: Dict[str, Path] = {}
    missing = []
    for name, candidates in required.items():
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            missing.append(" or ".join(str(candidate) for candidate in candidates))
        else:
            resolved[name] = path
    if missing:
        raise FileNotFoundError("Missing required analysis tables: " + ", ".join(missing))
    return {name: read_csv_rows(path) for name, path in resolved.items()}


def metric_row(name: str, value: Optional[float], threshold: str, inverse: bool = False) -> Dict[str, object]:
    status = "info"
    if value is not None:
        try:
            target = float(threshold[2:])
            if inverse:
                status = "pass" if value <= target else "warn"
            else:
                status = "pass" if value >= target else "warn"
        except Exception:
            status = "info"
    return {
        "metric_name": name,
        "metric_value": value,
        "threshold": threshold,
        "status": status,
        "notes": "",
    }


def build_quality_rows(
    run_dir: Path,
    module_rows: Sequence[Dict[str, str]],
    handoff_rows: Sequence[Dict[str, str]],
    trace_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
) -> List[Dict[str, object]]:
    quality_dir = run_dir / "analysis" / "quality"
    debug_dir = run_dir / "analysis" / "debug"
    rows: List[Dict[str, object]] = []

    trace_cov_path = quality_dir / "trace_coverage.csv"
    if trace_cov_path.exists():
        cov_rows = read_csv_rows(trace_cov_path)
        trace_nonzero = [to_float(r.get("trace_nonzero_ratio"), 0.0) or 0.0 for r in cov_rows]
        trace_valid = [to_float(r.get("trace_valid_true_ratio"), 0.0) or 0.0 for r in cov_rows]
        rows.append(metric_row("trace_coverage_mean", avg(trace_nonzero), ">=0.95"))
        rows.append(metric_row("trace_valid_mean", avg(trace_valid), ">=0.95"))

    complete_count = sum(1 for r in e2e_rows if to_int(r.get("complete_path"), 0) == 1)
    rows.append(metric_row("complete_path_ratio", complete_count / max(len(e2e_rows), 1), ">=0.85"))

    pair_fallback = sum(1 for r in module_rows if r.get("pair_method") not in ("trace_id", "proc_id"))
    rows.append(metric_row("phase_pair_fallback_ratio", pair_fallback / max(len(module_rows), 1), "<=0.10", inverse=True))

    origin_missing = sum(1 for r in trace_rows if to_int(r.get("sensor_origin_ns"), 0) == 0)
    rows.append(metric_row("origin_missing_ratio", origin_missing / max(len(trace_rows), 1), "<=0.10", inverse=True))

    unmatched_path = debug_dir / "handoff_unmatched.csv"
    unmatched_rows = read_csv_rows(unmatched_path) if unmatched_path.exists() else []
    rows.append(metric_row("handoff_unmatched_rate", len(unmatched_rows) / max(len(handoff_rows) + len(unmatched_rows), 1), "<=0.10", inverse=True))

    complete_anchors = [
        choose_anchor_ns(r, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns"))
        for r in e2e_rows if to_int(r.get("complete_path"), 0) == 1
    ]
    all_anchors = [choose_anchor_ns(r, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns")) for r in e2e_rows]
    complete_anchors = [v for v in complete_anchors if v]
    all_anchors = [v for v in all_anchors if v]
    if all_anchors and complete_anchors:
        startup_unstable_s = (min(complete_anchors) - min(all_anchors)) / 1e9
        rows.append(metric_row("startup_unstable_duration_s", max(startup_unstable_s, 0.0), "<=10.0", inverse=True))

    missing_stage_counter = Counter()
    for r in e2e_rows:
        if to_int(r.get("complete_path"), 0) != 1:
            for stage in (r.get("missing_stage") or "").split(","):
                stage = stage.strip()
                if stage:
                    missing_stage_counter[stage] += 1
    top_missing = "; ".join(f"{k}:{v}" for k, v in missing_stage_counter.most_common(5))
    rows.append({
        "metric_name": "missing_stage_topk",
        "metric_value": top_missing,
        "threshold": "informational",
        "status": "info",
        "notes": "Top missing stages among incomplete E2E samples",
    })
    return rows


def break_keys_for_stage(stage: str) -> Tuple[str, ...]:
    mapping = {
        "sensor_origin": ("fusion_input_ns", "fusion_output_ns"),
        "prediction_in": ("fusion_output_ns", "prediction_output_ns"),
        "prediction_out": ("prediction_output_ns", "prediction_input_ns", "fusion_output_ns"),
        "planning_in": ("planning_input_ns", "prediction_output_ns", "prediction_input_ns", "fusion_output_ns"),
        "planning_out": ("planning_output_ns", "planning_input_ns", "prediction_output_ns", "prediction_input_ns", "fusion_output_ns"),
        "first_control_consume": ("planning_output_ns", "first_control_consume_ns"),
        "first_control_output": ("first_control_consume_ns", "first_control_output_ns"),
        "control_consume": ("first_control_consume_ns", "planning_output_ns", "planning_input_ns", "prediction_output_ns", "prediction_input_ns", "fusion_output_ns"),
        "control_first_out": ("first_control_output_ns", "first_control_consume_ns", "planning_output_ns", "planning_input_ns", "prediction_output_ns", "prediction_input_ns", "fusion_output_ns"),
        "control_last_out": ("last_control_output_ns", "first_control_output_ns", "first_control_consume_ns", "planning_output_ns", "planning_input_ns", "prediction_output_ns", "prediction_input_ns", "fusion_output_ns"),
    }
    return mapping.get(stage, ("fusion_output_ns", "planning_input_ns", "first_control_consume_ns"))


def build_planning_no_output_drop_rows(
    run_dir: Path,
    complete_reference_times: Sequence[int],
    start_seq: int,
) -> Tuple[List[Dict[str, object]], set, int]:
    event_rows = [row for row in read_event_rows(run_dir, "planning") if row.get("module") == "planning"]
    if not event_rows:
        return [], set(), start_seq

    outputs_by_trace: Dict[str, List[int]] = defaultdict(list)
    for row in event_rows:
        if row.get("phase") != "output_pub":
            continue
        trace_id = row.get("trace_id") or ""
        out_ns = nonzero_int(row.get("mono_ns"))
        if trace_id and out_ns:
            outputs_by_trace[trace_id].append(out_ns)
    for values in outputs_by_trace.values():
        values.sort()

    normal_output_times = sorted({t for times in outputs_by_trace.values() for t in times})
    rows: List[Dict[str, object]] = []
    traces_with_no_output = set()
    seq = start_seq
    for row in event_rows:
        if row.get("phase") != "proc_enter":
            continue
        trace_id = row.get("trace_id") or ""
        enter_ns = nonzero_int(row.get("mono_ns"))
        if not trace_id or not enter_ns:
            continue
        matched_output = next((out_ns for out_ns in outputs_by_trace.get(trace_id, []) if out_ns >= enter_ns), None)
        if matched_output:
            continue
        # Avoid treating collection tail truncation as a real planning drop.
        _, resume_ns, _ = nearest_gap_context(normal_output_times or complete_reference_times, enter_ns)
        if not resume_ns:
            continue
        last_good_ns, resume_ns, gap_ms = nearest_gap_context(normal_output_times or complete_reference_times, enter_ns)
        expected_period_ms = EDGE_DEFAULT_PERIOD_MS["planning_internal"]
        missed_period_count = int(max(0, math.floor(gap_ms / expected_period_ms) - 1)) if gap_ms else ""
        rows.append({
            "drop_event_id": seq,
            "drop_type": "hard_drop",
            "break_stage": "planning_internal",
            "anchor_trace_id": trace_id,
            "parent_trace_id": "",
            "break_ns": enter_ns,
            "last_good_ns": last_good_ns or "",
            "resume_ns": resume_ns or "",
            "gap_ms": gap_ms,
            "expected_period_ms": expected_period_ms,
            "missed_period_count": missed_period_count,
            "evidence_type": "no_output_pub",
            "evidence_detail": (
                f"module=planning; proc_enter_ns={enter_ns}; output_pub_missing=true; "
                "allowed_no_output=false"
            ),
        })
        traces_with_no_output.add(trace_id)
        seq += 1
    return rows, traces_with_no_output, seq


def build_drop_events(
    run_dir: Path,
    handoff_rows: Sequence[Dict[str, str]],
    trace_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
    control_rows: Sequence[Dict[str, str]],
    soft_reuse_threshold: int = 4,
    stale_grace_periods: int = 1,
) -> List[Dict[str, object]]:
    debug_dir = run_dir / "analysis" / "debug"
    unmatched_path = debug_dir / "handoff_unmatched.csv"
    incomplete_path = debug_dir / "incomplete_e2e_frames.csv"
    unmatched_rows = read_csv_rows(unmatched_path) if unmatched_path.exists() else []
    incomplete_rows = read_csv_rows(incomplete_path) if incomplete_path.exists() else []

    matched_by_edge: Dict[str, List[int]] = defaultdict(list)
    for row in handoff_rows:
        edge = row.get("edge_name") or "unknown"
        src = nonzero_int(row.get("mono_ns_src"))
        if src:
            matched_by_edge[edge].append(src)
    for edge in matched_by_edge:
        matched_by_edge[edge].sort()
    period_by_edge = {edge: median_period_ms(times) or EDGE_DEFAULT_PERIOD_MS.get(edge, 80.0) for edge, times in matched_by_edge.items()}

    complete_reference_times = sorted({
        choose_anchor_ns(r, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns"))
        for r in e2e_rows if to_int(r.get("complete_path"), 0) == 1
    } - {None})
    e2e_period_ms = median_period_ms(complete_reference_times) or 80.0

    control_times = sorted({nonzero_int(r.get("first_control_consume_ns")) for r in control_rows} - {None})
    control_period_ms = median_period_ms(control_times) or EDGE_DEFAULT_PERIOD_MS["control_consume_or_reuse"]

    drop_events: List[Dict[str, object]] = []
    seq = 1

    for row in unmatched_rows:
        edge = row.get("edge_name") or "unknown"
        break_ns = nonzero_int(row.get("mono_ns_src")) or 0
        ref_times = matched_by_edge.get(edge, [])
        last_good_ns, resume_ns, gap_ms = nearest_gap_context(ref_times, break_ns)
        expected_period_ms = period_by_edge.get(edge, EDGE_DEFAULT_PERIOD_MS.get(edge, 80.0))
        missed_period_count = int(max(0, math.floor(gap_ms / expected_period_ms) - 1)) if gap_ms and expected_period_ms else ""
        drop_events.append({
            "drop_event_id": seq,
            "drop_type": "hard_drop",
            "break_stage": edge,
            "anchor_trace_id": row.get("trace_id", ""),
            "parent_trace_id": "",
            "break_ns": break_ns,
            "last_good_ns": last_good_ns or "",
            "resume_ns": resume_ns or "",
            "gap_ms": gap_ms,
            "expected_period_ms": expected_period_ms,
            "missed_period_count": missed_period_count,
            "evidence_type": row.get("reason", "no_dst_in"),
            "evidence_detail": f"edge={edge}; event_id_src={row.get('event_id_src','')}; max_window_ms={row.get('max_window_ms','')}",
        })
        seq += 1

    planning_no_output_rows, no_output_traces, seq = build_planning_no_output_drop_rows(
        run_dir,
        complete_reference_times,
        seq,
    )
    drop_events.extend(planning_no_output_rows)

    seen_incomplete = set()
    for row in incomplete_rows:
        missing_stage_text = row.get("missing_stage") or ""
        stages = [s.strip() for s in missing_stage_text.split(",") if s.strip()]
        if not stages:
            continue
        fusion_trace_id = row.get("fusion_trace_id", "")
        parent_trace_id = row.get("parent_trace_id", "")
        for stage in stages:
            if stage == "planning_out" and fusion_trace_id in no_output_traces:
                continue
            if stage == "first_control_consume" and nonzero_int(row.get("planning_output_ns")):
                continue
            drop_type, break_stage = INCOMPLETE_STAGE_TO_BREAK.get(stage, ("hard_drop", stage))
            key = (fusion_trace_id if drop_type != "parent_missing" else parent_trace_id, stage, drop_type)
            if key in seen_incomplete:
                continue
            seen_incomplete.add(key)
            break_ns = choose_anchor_ns(row, break_keys_for_stage(stage)) or 0
            last_good_ns, resume_ns, gap_ms = nearest_gap_context(complete_reference_times, break_ns)
            expected_period_ms = EDGE_DEFAULT_PERIOD_MS.get(break_stage, e2e_period_ms)
            missed_period_count = int(max(0, math.floor(gap_ms / expected_period_ms) - 1)) if gap_ms and expected_period_ms else ""
            drop_events.append({
                "drop_event_id": seq,
                "drop_type": drop_type,
                "break_stage": break_stage,
                "anchor_trace_id": fusion_trace_id,
                "parent_trace_id": parent_trace_id if drop_type == "parent_missing" else "",
                "break_ns": break_ns,
                "last_good_ns": last_good_ns or "",
                "resume_ns": resume_ns or "",
                "gap_ms": gap_ms,
                "expected_period_ms": expected_period_ms,
                "missed_period_count": missed_period_count,
                "evidence_type": stage,
                "evidence_detail": f"complete_path=0; sensor_kind={row.get('sensor_kind','')}; missing_stage={missing_stage_text}",
            })
            seq += 1

    consumed_by_trace = control_rows_by_trace(control_rows)
    for row in planning_output_rows(e2e_rows):
        fusion_trace_id = row.get("fusion_trace_id") or ""
        if not fusion_trace_id or fusion_trace_id in consumed_by_trace:
            continue
        planning_out_ns = nonzero_int(row.get("planning_output_ns"))
        if not planning_out_ns:
            continue
        previous_control = find_previous_control_reuse(control_rows, planning_out_ns)
        next_control = find_next_control_consume(control_rows, planning_out_ns)
        reused_trace = previous_control.get("fusion_trace_id", "") if previous_control else ""
        next_trace = next_control.get("fusion_trace_id", "") if next_control else ""
        evidence_type = "stale_replaced" if next_trace and next_trace != fusion_trace_id else "unused_by_control"
        last_good_ns, resume_ns, gap_ms = nearest_gap_context(complete_reference_times, planning_out_ns)
        missed_period_count = int(max(0, math.floor(gap_ms / control_period_ms) - 1)) if gap_ms and control_period_ms else ""
        drop_events.append({
            "drop_event_id": seq,
            "drop_type": "soft_drop",
            "break_stage": "control_consume_or_reuse",
            "anchor_trace_id": fusion_trace_id,
            "parent_trace_id": "",
            "break_ns": planning_out_ns,
            "last_good_ns": last_good_ns or "",
            "resume_ns": resume_ns or "",
            "gap_ms": gap_ms,
            "expected_period_ms": control_period_ms,
            "missed_period_count": missed_period_count,
            "evidence_type": evidence_type,
            "evidence_detail": (
                f"planning_output_ns={planning_out_ns}; unused_by_control=true; "
                f"previous_reused_trace={reused_trace}; next_consumed_trace={next_trace}; confidence=strong"
            ),
        })
        seq += 1

    planning_outputs = planning_output_rows(e2e_rows)
    stale_grace_ns = int(max(stale_grace_periods, 0) * control_period_ms * 1e6)
    for row in control_rows:
        reuse_count = to_int(row.get("control_reuse_count"), 0)
        if reuse_count < soft_reuse_threshold:
            continue
        break_ns = nonzero_int(row.get("first_control_consume_ns")) or 0
        first_output = nonzero_int(row.get("first_control_output_ns")) or 0
        last_output = nonzero_int(row.get("last_control_output_ns")) or 0
        reuse_tail_ms = ((last_output - first_output) / 1e6) if first_output and last_output and last_output >= first_output else None
        newer_outputs = [
            p for p in planning_outputs
            if (p.get("fusion_trace_id") or "") != (row.get("fusion_trace_id") or "")
            and (nonzero_int(p.get("planning_output_ns")) or 0) > first_output
            and (nonzero_int(p.get("planning_output_ns")) or 0) + stale_grace_ns < last_output
        ]
        if not newer_outputs:
            continue
        first_stale = newer_outputs[0]
        first_stale_ns = nonzero_int(first_stale.get("planning_output_ns")) or first_output
        drop_events.append({
            "drop_event_id": seq,
            "drop_type": "soft_drop",
            "break_stage": "control_consume_or_reuse",
            "anchor_trace_id": row.get("fusion_trace_id", ""),
            "parent_trace_id": "",
            "break_ns": break_ns,
            "last_good_ns": "",
            "resume_ns": "",
            "gap_ms": reuse_tail_ms,
            "expected_period_ms": control_period_ms,
            "missed_period_count": max(reuse_count - 1, 0),
            "evidence_type": "stale_reuse",
            "evidence_detail": (
                f"control_reuse_count={reuse_count}; first_control_proc_id={row.get('first_control_proc_id','')}; "
                f"last_control_proc_id={row.get('last_control_proc_id','')}; "
                f"newer_planning_trace={first_stale.get('fusion_trace_id','')}; newer_planning_output_ns={first_stale_ns}; "
                f"stale_grace_periods={stale_grace_periods}; confidence=strong"
            ),
        })
        seq += 1

    drop_events.sort(key=lambda r: (to_int(r.get("break_ns"), 0), to_int(r.get("drop_event_id"), 0)))
    return drop_events


def build_latency_timeline(
    module_rows: Sequence[Dict[str, str]],
    handoff_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
    control_rows: Sequence[Dict[str, str]],
    run_start_ns: int,
    bin_seconds: int,
    deadline_config: Optional[Dict[str, Dict[str, object]]] = None,
) -> List[Dict[str, object]]:
    bins: Dict[int, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    meta_counts: Dict[int, Counter] = defaultdict(Counter)

    for row in e2e_rows:
        anchor = choose_anchor_ns(row, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns"))
        if not anchor:
            continue
        b = bin_seconds_for_ns(anchor, run_start_ns, bin_seconds)
        meta_counts[b]["sample_count"] += 1
        if to_int(row.get("complete_path"), 0) == 1:
            meta_counts[b]["complete_count"] += 1
        rt = to_float(row.get("reaction_time_ms"))
        age = to_float(row.get("data_lifetime_ms")) or to_float(row.get("data_age_ms"))
        if rt is not None:
            bins[b]["rt"].append(rt)
        if age is not None:
            bins[b]["data_age"].append(age)

    for row in module_rows:
        if row.get("module") != "planning" or row.get("phase_label") != "total":
            continue
        anchor = nonzero_int(row.get("enter_ns"))
        lat = to_float(row.get("latency_ms"))
        if anchor and lat is not None:
            b = bin_seconds_for_ns(anchor, run_start_ns, bin_seconds)
            bins[b]["planning_total"].append(lat)

    for row in handoff_rows:
        if row.get("edge_name") != "planning_to_control":
            continue
        anchor = nonzero_int(row.get("mono_ns_src"))
        handoff_ms = to_float(row.get("handoff_ms"))
        if anchor and handoff_ms is not None:
            b = bin_seconds_for_ns(anchor, run_start_ns, bin_seconds)
            bins[b]["planning_wait"].append(handoff_ms)

    for row in control_rows:
        anchor = nonzero_int(row.get("first_control_consume_ns"))
        reuse_count = to_float(row.get("control_reuse_count"))
        if anchor and reuse_count is not None:
            b = bin_seconds_for_ns(anchor, run_start_ns, bin_seconds)
            bins[b]["reuse"].append(reuse_count)

    rows: List[Dict[str, object]] = []
    all_bins = sorted(set(bins.keys()) | set(meta_counts.keys()))
    rt_threshold = deadline_threshold(deadline_config, "e2e_rt_deadline")
    age_threshold = deadline_threshold(deadline_config, "e2e_data_age_deadline")
    planning_threshold = deadline_threshold(deadline_config, "planning_total_deadline")
    wait_threshold = deadline_threshold(deadline_config, "planning_to_control_deadline")
    for b in all_bins:
        rt_values = bins[b].get("rt", [])
        age_values = bins[b].get("data_age", [])
        planning_values = bins[b].get("planning_total", [])
        wait_values = bins[b].get("planning_wait", [])
        reuse_values = bins[b].get("reuse", [])
        rt_miss = miss_summary(rt_values, rt_threshold)
        age_miss = miss_summary(age_values, age_threshold)
        planning_miss = miss_summary(planning_values, planning_threshold)
        wait_miss = miss_summary(wait_values, wait_threshold)
        rows.append({
            "time_bin_s": b,
            "sample_count": meta_counts[b].get("sample_count", 0),
            "complete_count": meta_counts[b].get("complete_count", 0),
            "rt_p50": percentile(rt_values, 50),
            "rt_p95": percentile(rt_values, 95),
            "rt_p99": percentile(rt_values, 99),
            "rt_miss_count": rt_miss["miss_count"],
            "rt_miss_rate_pct": rt_miss["miss_rate_pct"],
            "data_age_p50": percentile(age_values, 50),
            "data_age_p95": percentile(age_values, 95),
            "data_age_p99": percentile(age_values, 99),
            "data_age_miss_count": age_miss["miss_count"],
            "data_age_miss_rate_pct": age_miss["miss_rate_pct"],
            "planning_total_p50": percentile(planning_values, 50),
            "planning_total_p95": percentile(planning_values, 95),
            "planning_total_p99": percentile(planning_values, 99),
            "planning_total_miss_count": planning_miss["miss_count"],
            "planning_total_miss_rate_pct": planning_miss["miss_rate_pct"],
            "planning_wait_p95": percentile(wait_values, 95),
            "planning_wait_p99": percentile(wait_values, 99),
            "planning_wait_miss_count": wait_miss["miss_count"],
            "planning_wait_miss_rate_pct": wait_miss["miss_rate_pct"],
            "reuse_p95": percentile(reuse_values, 95),
            "reuse_p99": percentile(reuse_values, 99),
        })
    return rows


def is_high_latency(
    row: Dict[str, object],
    rt_t: Optional[float],
    age_t: Optional[float],
    plan_t: Optional[float],
    wait_t: Optional[float],
    reuse_t: Optional[float],
    rt_p99_t: Optional[float] = None,
    age_p99_t: Optional[float] = None,
    plan_p99_t: Optional[float] = None,
    wait_p99_t: Optional[float] = None,
    reuse_p99_t: Optional[float] = None,
) -> bool:
    pairs = [
        (to_float(row.get("rt_p95")), rt_t),
        (to_float(row.get("rt_p99")), rt_p99_t),
        (to_float(row.get("data_age_p95")), age_t),
        (to_float(row.get("data_age_p99")), age_p99_t),
        (to_float(row.get("planning_total_p95")), plan_t),
        (to_float(row.get("planning_total_p99")), plan_p99_t),
        (to_float(row.get("planning_wait_p95")), wait_t),
        (to_float(row.get("planning_wait_p99")), wait_p99_t),
        (to_float(row.get("reuse_p95")), reuse_t),
        (to_float(row.get("reuse_p99")), reuse_p99_t),
    ]
    for value, threshold in pairs:
        if value is not None and threshold is not None and value >= threshold:
            return True
    return False


def build_latency_drop_alignment(
    latency_rows: Sequence[Dict[str, str]],
    drop_rows: Sequence[Dict[str, str]],
    run_start_ns: int,
    bin_seconds: int,
) -> List[Dict[str, object]]:
    latency_by_bin = {to_int(r.get("time_bin_s"), 0): r for r in latency_rows}
    drop_counts: Dict[int, Counter] = defaultdict(Counter)
    for row in drop_rows:
        break_ns = nonzero_int(row.get("break_ns"))
        if not break_ns:
            continue
        b = bin_seconds_for_ns(break_ns, run_start_ns, bin_seconds)
        drop_counts[b]["total"] += 1
        stage = row.get("break_stage") or "unknown"
        drop_counts[b][stage] += 1

    global_rt_threshold = percentile([to_float(r.get("rt_p95")) for r in latency_rows if to_float(r.get("rt_p95")) is not None], 95)
    global_age_threshold = percentile([to_float(r.get("data_age_p95")) for r in latency_rows if to_float(r.get("data_age_p95")) is not None], 95)
    global_plan_threshold = percentile([to_float(r.get("planning_total_p95")) for r in latency_rows if to_float(r.get("planning_total_p95")) is not None], 95)
    global_wait_threshold = percentile([to_float(r.get("planning_wait_p95")) for r in latency_rows if to_float(r.get("planning_wait_p95")) is not None], 95)
    global_reuse_threshold = percentile([to_float(r.get("reuse_p95")) for r in latency_rows if to_float(r.get("reuse_p95")) is not None], 95)
    global_rt_p99_threshold = percentile([to_float(r.get("rt_p99")) for r in latency_rows if to_float(r.get("rt_p99")) is not None], 95)
    global_age_p99_threshold = percentile([to_float(r.get("data_age_p99")) for r in latency_rows if to_float(r.get("data_age_p99")) is not None], 95)
    global_plan_p99_threshold = percentile([to_float(r.get("planning_total_p99")) for r in latency_rows if to_float(r.get("planning_total_p99")) is not None], 95)
    global_wait_p99_threshold = percentile([to_float(r.get("planning_wait_p99")) for r in latency_rows if to_float(r.get("planning_wait_p99")) is not None], 95)
    global_reuse_p99_threshold = percentile([to_float(r.get("reuse_p99")) for r in latency_rows if to_float(r.get("reuse_p99")) is not None], 95)

    all_bins = sorted(set(latency_by_bin.keys()) | set(drop_counts.keys()))
    rows: List[Dict[str, object]] = []
    for idx, b in enumerate(all_bins):
        lat = latency_by_bin.get(b, {})
        drops = drop_counts.get(b, Counter())
        high_now = is_high_latency(
            lat,
            global_rt_threshold,
            global_age_threshold,
            global_plan_threshold,
            global_wait_threshold,
            global_reuse_threshold,
            global_rt_p99_threshold,
            global_age_p99_threshold,
            global_plan_p99_threshold,
            global_wait_p99_threshold,
            global_reuse_p99_threshold,
        )
        next_lat = latency_by_bin.get(all_bins[idx + 1], {}) if idx + 1 < len(all_bins) else {}
        next_high = is_high_latency(
            next_lat,
            global_rt_threshold,
            global_age_threshold,
            global_plan_threshold,
            global_wait_threshold,
            global_reuse_threshold,
            global_rt_p99_threshold,
            global_age_p99_threshold,
            global_plan_p99_threshold,
            global_wait_p99_threshold,
            global_reuse_p99_threshold,
        )
        next_drop = drop_counts.get(all_bins[idx + 1], Counter()) if idx + 1 < len(all_bins) else Counter()

        if drops.get("total", 0) > 0 and high_now:
            corr_tag = "drop_and_latency_high"
            lead_lag_tag = "coincident"
        elif drops.get("total", 0) > 0 and next_high:
            corr_tag = "drop_precedes_latency"
            lead_lag_tag = "drop_leads_latency"
        elif high_now and next_drop.get("total", 0) > 0:
            corr_tag = "latency_precedes_drop"
            lead_lag_tag = "latency_leads_drop"
        else:
            corr_tag = "none"
            lead_lag_tag = "none"

        stage_counts = {k: v for k, v in drops.items() if k != "total"}
        rt_miss_rate = to_float(lat.get("rt_miss_rate_pct"))
        age_miss_rate = to_float(lat.get("data_age_miss_rate_pct"))
        rt_p99 = to_float(lat.get("rt_p99"))
        age_p99 = to_float(lat.get("data_age_p99"))
        rt_miss_p99_alignment = (
            "miss_and_p99_high" if rt_miss_rate and rt_miss_rate > 0 and rt_p99 is not None and global_rt_p99_threshold is not None and rt_p99 >= global_rt_p99_threshold
            else "miss_without_p99_high" if rt_miss_rate and rt_miss_rate > 0
            else "no_miss"
        )
        data_age_miss_p99_alignment = (
            "miss_and_p99_high" if age_miss_rate and age_miss_rate > 0 and age_p99 is not None and global_age_p99_threshold is not None and age_p99 >= global_age_p99_threshold
            else "miss_without_p99_high" if age_miss_rate and age_miss_rate > 0
            else "no_miss"
        )
        rows.append({
            "time_bin_s": b,
            "drop_count_total": drops.get("total", 0),
            "drop_count_by_stage": stage_counts,
            "rt_p95": lat.get("rt_p95", ""),
            "rt_p99": lat.get("rt_p99", ""),
            "rt_miss_rate_pct": lat.get("rt_miss_rate_pct", ""),
            "data_age_p95": lat.get("data_age_p95", ""),
            "data_age_p99": lat.get("data_age_p99", ""),
            "data_age_miss_rate_pct": lat.get("data_age_miss_rate_pct", ""),
            "planning_total_p95": lat.get("planning_total_p95", ""),
            "planning_total_p99": lat.get("planning_total_p99", ""),
            "planning_total_miss_rate_pct": lat.get("planning_total_miss_rate_pct", ""),
            "planning_wait_p95": lat.get("planning_wait_p95", ""),
            "planning_wait_p99": lat.get("planning_wait_p99", ""),
            "planning_wait_miss_rate_pct": lat.get("planning_wait_miss_rate_pct", ""),
            "reuse_p95": lat.get("reuse_p95", ""),
            "reuse_p99": lat.get("reuse_p99", ""),
            "rt_miss_p99_alignment": rt_miss_p99_alignment,
            "data_age_miss_p99_alignment": data_age_miss_p99_alignment,
            "corr_tag": corr_tag,
            "lead_lag_tag": lead_lag_tag,
        })
    return rows


def max_value(values: Sequence[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return max(clean) if clean else None


def summarize_values(values: Sequence[float]) -> Dict[str, Optional[float]]:
    return {
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max_value(values),
    }


def miss_summary(values: Sequence[float], threshold_ms: Optional[float]) -> Dict[str, object]:
    clean = [v for v in values if v is not None]
    if threshold_ms is None:
        return {"miss_count": "", "miss_rate_pct": ""}
    miss_count = sum(1 for v in clean if v > threshold_ms)
    return {
        "miss_count": miss_count,
        "miss_rate_pct": (100.0 * miss_count / len(clean)) if clean else "",
    }


def deadline_threshold(deadline_config: Optional[Dict[str, Dict[str, object]]], metric_name: str) -> Optional[float]:
    if not deadline_config or metric_name not in deadline_config:
        return None
    return to_float(deadline_config[metric_name].get("threshold_ms"))


def build_steady_state_summary(
    module_rows: Sequence[Dict[str, str]],
    handoff_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
    control_rows: Sequence[Dict[str, str]],
    drop_rows: Sequence[Dict[str, object]],
    run_start_ns: int,
    steady_start_s: Optional[float],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    scopes = [("raw", None), ("steady", steady_start_s)]
    for scope, start_s in scopes:
        scoped_e2e = [
            row for row in e2e_rows
            if is_at_or_after_s(choose_anchor_ns(row, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns")), run_start_ns, start_s)
        ]
        complete_e2e = [row for row in scoped_e2e if to_int(row.get("complete_path"), 0) == 1]
        scoped_modules = [
            row for row in module_rows
            if is_at_or_after_s(nonzero_int(row.get("enter_ns")), run_start_ns, start_s)
        ]
        scoped_handoffs = [
            row for row in handoff_rows
            if is_at_or_after_s(nonzero_int(row.get("mono_ns_src")), run_start_ns, start_s)
        ]
        scoped_controls = [
            row for row in control_rows
            if is_at_or_after_s(nonzero_int(row.get("first_control_consume_ns")), run_start_ns, start_s)
        ]
        scoped_drops = [
            row for row in drop_rows
            if is_at_or_after_s(nonzero_int(row.get("break_ns")), run_start_ns, start_s)
        ]
        rt_stats = summarize_values([to_float(row.get("reaction_time_ms")) for row in complete_e2e])
        age_stats = summarize_values([
            to_float(row.get("data_lifetime_ms")) or to_float(row.get("data_age_ms"))
            for row in complete_e2e
        ])
        planning_stats = summarize_values([
            to_float(row.get("latency_ms"))
            for row in scoped_modules
            if row.get("module") == "planning" and row.get("phase_label") == "total"
        ])
        wait_stats = summarize_values([
            to_float(row.get("handoff_ms"))
            for row in scoped_handoffs
            if row.get("edge_name") == "planning_to_control"
        ])
        reuse_stats = summarize_values([to_float(row.get("control_reuse_count")) for row in scoped_controls])
        rows.append({
            "scope": scope,
            "steady_start_s": start_s if start_s is not None else "",
            "sample_count": len(scoped_e2e),
            "complete_count": len(complete_e2e),
            "drop_count_total": len(scoped_drops),
            "rt_p50": rt_stats["p50"],
            "rt_p95": rt_stats["p95"],
            "rt_p99": rt_stats["p99"],
            "rt_max": rt_stats["max"],
            "data_age_p50": age_stats["p50"],
            "data_age_p95": age_stats["p95"],
            "data_age_p99": age_stats["p99"],
            "data_age_max": age_stats["max"],
            "planning_total_p95": planning_stats["p95"],
            "planning_total_p99": planning_stats["p99"],
            "planning_wait_p95": wait_stats["p95"],
            "planning_wait_p99": wait_stats["p99"],
            "reuse_p95": reuse_stats["p95"],
            "reuse_p99": reuse_stats["p99"],
        })
    return rows


def deadline_row(
    scope: str,
    metric_name: str,
    obj: str,
    start_anchor: str,
    end_anchor: str,
    config: Dict[str, object],
    values: Sequence[float],
) -> Dict[str, object]:
    threshold_ms = float(config["threshold_ms"])
    clean = [v for v in values if v is not None]
    miss_count = sum(1 for v in clean if v > threshold_ms)
    eligible_count = len(clean)
    return {
        "scope": scope,
        "metric_name": metric_name,
        "object": obj,
        "start_anchor": start_anchor,
        "end_anchor": end_anchor,
        "threshold_ms": threshold_ms,
        "threshold_source": config.get("threshold_source", ""),
        "base_period_ms": config.get("base_period_ms", ""),
        "eligible_count": eligible_count,
        "miss_count": miss_count,
        "miss_rate_pct": (100.0 * miss_count / eligible_count) if eligible_count else "",
    }


def build_deadline_metrics(
    module_rows: Sequence[Dict[str, str]],
    handoff_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
    run_start_ns: int,
    steady_start_s: Optional[float],
    deadline_config: Dict[str, Dict[str, object]],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for scope, start_s in (("raw", None), ("steady", steady_start_s)):
        planning_values = [
            to_float(row.get("latency_ms"))
            for row in module_rows
            if row.get("module") == "planning"
            and row.get("phase_label") == "total"
            and is_at_or_after_s(nonzero_int(row.get("enter_ns")), run_start_ns, start_s)
        ]
        handoff_values = [
            to_float(row.get("handoff_ms"))
            for row in handoff_rows
            if row.get("edge_name") == "planning_to_control"
            and is_at_or_after_s(nonzero_int(row.get("mono_ns_src")), run_start_ns, start_s)
        ]
        e2e_values = [
            to_float(row.get("reaction_time_ms"))
            for row in e2e_rows
            if to_int(row.get("complete_path"), 0) == 1
            and is_at_or_after_s(choose_anchor_ns(row, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns")), run_start_ns, start_s)
        ]
        data_age_values = [
            to_float(row.get("data_lifetime_ms")) or to_float(row.get("data_age_ms"))
            for row in e2e_rows
            if to_int(row.get("complete_path"), 0) == 1
            and is_at_or_after_s(choose_anchor_ns(row, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns")), run_start_ns, start_s)
        ]
        rows.extend([
            deadline_row(
                scope,
                "planning_total_deadline",
                "planning.total",
                "proc_enter",
                "output_pub",
                deadline_config["planning_total_deadline"],
                planning_values,
            ),
            deadline_row(
                scope,
                "planning_to_control_deadline",
                "planning_to_control",
                "planning_out",
                "control_in",
                deadline_config["planning_to_control_deadline"],
                handoff_values,
            ),
            deadline_row(
                scope,
                "e2e_rt_deadline",
                "e2e.reaction_time",
                "sensor_origin",
                "first_control_consume",
                deadline_config["e2e_rt_deadline"],
                e2e_values,
            ),
            deadline_row(
                scope,
                "e2e_data_age_deadline",
                "e2e.data_age",
                "sensor_origin",
                "last_control_output",
                deadline_config["e2e_data_age_deadline"],
                data_age_values,
            ),
        ])
    return rows


def lookup_phase_by_trace(module_rows: Sequence[Dict[str, str]]) -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = defaultdict(dict)
    for row in module_rows:
        trace_id = row.get("fusion_trace_id") or row.get("trace_id") or ""
        if not trace_id:
            continue
        key = f"{row.get('module')}_{row.get('phase_label')}"
        lat = to_float(row.get("latency_ms"))
        if lat is not None:
            result[trace_id][key] = lat
    return result


def lookup_handoff_by_trace(handoff_rows: Sequence[Dict[str, str]]) -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = defaultdict(dict)
    for row in handoff_rows:
        trace_id = row.get("trace_id") or ""
        edge = row.get("edge_name") or ""
        lat = to_float(row.get("handoff_ms"))
        if trace_id and edge and lat is not None:
            result[trace_id][edge] = lat
    return result


def classify_anomaly_root_cause(
    complete_path: int,
    planning_ms: Optional[float],
    planning_wait_ms: Optional[float],
    reuse_tail_ms: Optional[float],
    reuse_count: Optional[float],
    thresholds: Dict[str, Optional[float]],
) -> str:
    if complete_path != 1:
        return "missing_path"
    if planning_ms is not None and (planning_ms >= 80.0 or (thresholds.get("planning_p99") is not None and planning_ms >= thresholds["planning_p99"])):
        return "planning_slow"
    if planning_wait_ms is not None and (planning_wait_ms >= 15.0 or (thresholds.get("wait_p99") is not None and planning_wait_ms >= thresholds["wait_p99"])):
        return "handoff_wait"
    if (
        reuse_count is not None
        and thresholds.get("reuse_p99") is not None
        and reuse_count >= thresholds["reuse_p99"]
    ) or (reuse_tail_ms is not None and reuse_tail_ms >= 80.0):
        return "reuse_tail"
    return "mixed"


def build_anomaly_frame_table(
    module_rows: Sequence[Dict[str, str]],
    handoff_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
    control_rows: Sequence[Dict[str, str]],
    run_start_ns: int,
    steady_start_s: Optional[float],
    top_n: int,
) -> List[Dict[str, object]]:
    phase_by_trace = lookup_phase_by_trace(module_rows)
    handoff_by_trace = lookup_handoff_by_trace(handoff_rows)
    control_by_trace = control_rows_by_trace(control_rows)
    unique_e2e = list(e2e_rows_by_fusion(e2e_rows).values())
    steady_complete = [
        row for row in unique_e2e
        if to_int(row.get("complete_path"), 0) == 1
        and is_at_or_after_s(choose_anchor_ns(row, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns")), run_start_ns, steady_start_s)
    ]
    thresholds = {
        "rt_p99": percentile([to_float(row.get("reaction_time_ms")) for row in steady_complete], 99),
        "age_p99": percentile([
            to_float(row.get("data_lifetime_ms")) or to_float(row.get("data_age_ms"))
            for row in steady_complete
        ], 99),
        "planning_p99": percentile([
            to_float(row.get("latency_ms"))
            for row in module_rows
            if row.get("module") == "planning"
            and row.get("phase_label") == "total"
            and is_at_or_after_s(nonzero_int(row.get("enter_ns")), run_start_ns, steady_start_s)
        ], 99),
        "wait_p99": percentile([
            to_float(row.get("handoff_ms"))
            for row in handoff_rows
            if row.get("edge_name") == "planning_to_control"
            and is_at_or_after_s(nonzero_int(row.get("mono_ns_src")), run_start_ns, steady_start_s)
        ], 99),
        "reuse_p99": percentile([
            to_float(row.get("control_reuse_count"))
            for row in control_rows
            if is_at_or_after_s(nonzero_int(row.get("first_control_consume_ns")), run_start_ns, steady_start_s)
        ], 99),
    }

    candidates: List[Dict[str, object]] = []
    for row in unique_e2e:
        fusion_trace_id = row.get("fusion_trace_id") or ""
        anchor_ns = choose_anchor_ns(row, ("sensor_origin_ns", "fusion_input_ns", "fusion_output_ns"))
        is_steady = is_at_or_after_s(anchor_ns, run_start_ns, steady_start_s)
        rt = to_float(row.get("reaction_time_ms"))
        age = to_float(row.get("data_lifetime_ms")) or to_float(row.get("data_age_ms"))
        rt_anomaly = rt is not None and thresholds["rt_p99"] is not None and rt > thresholds["rt_p99"]
        age_anomaly = age is not None and thresholds["age_p99"] is not None and age > thresholds["age_p99"]
        if not rt_anomaly and not age_anomaly:
            continue
        phase = phase_by_trace.get(fusion_trace_id, {})
        handoff = handoff_by_trace.get(fusion_trace_id, {})
        control = control_by_trace.get(fusion_trace_id, {})
        first_output = nonzero_int(control.get("first_control_output_ns")) or nonzero_int(row.get("first_control_output_ns"))
        last_output = nonzero_int(control.get("last_control_output_ns")) or nonzero_int(row.get("last_control_output_ns"))
        reuse_tail = ((last_output - first_output) / 1e6) if first_output and last_output and last_output >= first_output else None
        reuse_count = to_float(control.get("control_reuse_count")) or to_float(row.get("control_reuse_count"))
        planning_ms = phase.get("planning_total")
        planning_wait_ms = handoff.get("planning_to_control")
        root_cause = classify_anomaly_root_cause(
            to_int(row.get("complete_path"), 0),
            planning_ms,
            planning_wait_ms,
            reuse_tail,
            reuse_count,
            thresholds,
        )
        score = max(
            (rt / thresholds["rt_p99"]) if rt and thresholds["rt_p99"] else 0,
            (age / thresholds["age_p99"]) if age and thresholds["age_p99"] else 0,
        )
        anomaly_type = "+".join(name for name, flag in (("RT", rt_anomaly), ("DataAge", age_anomaly)) if flag)
        candidates.append({
            "fusion_trace_id": fusion_trace_id,
            "sensor_kind": row.get("sensor_kind", ""),
            "anchor_ns": anchor_ns or "",
            "relative_s": relative_s(anchor_ns, run_start_ns),
            "is_steady": is_steady,
            "reaction_time_ms": rt,
            "data_age_ms": age,
            "rt_threshold_ms": thresholds["rt_p99"],
            "data_age_threshold_ms": thresholds["age_p99"],
            "anomaly_type": anomaly_type,
            "severity_score": score,
            "root_cause_tag": root_cause,
            "planning_total_ms": planning_ms,
            "planning_wait_ms": planning_wait_ms,
            "reuse_tail_ms": reuse_tail,
            "control_reuse_count": reuse_count,
            "complete_path": to_int(row.get("complete_path"), 0),
            "evidence": (
                f"planning_total={planning_ms}; planning_wait={planning_wait_ms}; "
                f"reuse_tail={reuse_tail}; reuse_count={reuse_count}"
            ),
        })
    candidates.sort(key=lambda row: (to_float(row.get("severity_score"), 0.0) or 0.0), reverse=True)
    return candidates[:top_n]
