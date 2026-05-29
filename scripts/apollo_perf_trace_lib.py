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


def load_required_tables(run_dir: Path) -> Dict[str, List[Dict[str, str]]]:
    tables_dir = run_dir / "analysis" / "tables"
    required = {
        "module_phase": tables_dir / "module_phase_table.csv",
        "handoff": tables_dir / "message_handoff_detail.csv",
        "trace_link": tables_dir / "trace_link_table.csv",
        "e2e": tables_dir / "e2e_frame_table.csv",
        "control_usage": tables_dir / "control_usage.csv",
    }
    missing = [str(path) for path in required.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required analysis tables: " + ", ".join(missing))
    return {name: read_csv_rows(path) for name, path in required.items()}


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


def build_drop_events(
    run_dir: Path,
    handoff_rows: Sequence[Dict[str, str]],
    trace_rows: Sequence[Dict[str, str]],
    e2e_rows: Sequence[Dict[str, str]],
    control_rows: Sequence[Dict[str, str]],
    soft_reuse_threshold: int = 4,
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

    seen_incomplete = set()
    for row in incomplete_rows:
        missing_stage_text = row.get("missing_stage") or ""
        stages = [s.strip() for s in missing_stage_text.split(",") if s.strip()]
        if not stages:
            continue
        fusion_trace_id = row.get("fusion_trace_id", "")
        parent_trace_id = row.get("parent_trace_id", "")
        for stage in stages:
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

    for row in control_rows:
        reuse_count = to_int(row.get("control_reuse_count"), 0)
        if reuse_count < soft_reuse_threshold:
            continue
        break_ns = nonzero_int(row.get("first_control_consume_ns")) or 0
        first_output = nonzero_int(row.get("first_control_output_ns")) or 0
        last_output = nonzero_int(row.get("last_control_output_ns")) or 0
        reuse_tail_ms = ((last_output - first_output) / 1e6) if first_output and last_output and last_output >= first_output else None
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
            "evidence_detail": f"control_reuse_count={reuse_count}; first_control_proc_id={row.get('first_control_proc_id','')}; last_control_proc_id={row.get('last_control_proc_id','')}",
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
    for b in all_bins:
        rows.append({
            "time_bin_s": b,
            "sample_count": meta_counts[b].get("sample_count", 0),
            "complete_count": meta_counts[b].get("complete_count", 0),
            "rt_p50": percentile(bins[b].get("rt", []), 50),
            "rt_p95": percentile(bins[b].get("rt", []), 95),
            "rt_p99": percentile(bins[b].get("rt", []), 99),
            "data_age_p50": percentile(bins[b].get("data_age", []), 50),
            "data_age_p95": percentile(bins[b].get("data_age", []), 95),
            "data_age_p99": percentile(bins[b].get("data_age", []), 99),
            "planning_total_p50": percentile(bins[b].get("planning_total", []), 50),
            "planning_total_p95": percentile(bins[b].get("planning_total", []), 95),
            "planning_wait_p95": percentile(bins[b].get("planning_wait", []), 95),
            "reuse_p95": percentile(bins[b].get("reuse", []), 95),
        })
    return rows


def is_high_latency(
    row: Dict[str, object],
    rt_t: Optional[float],
    age_t: Optional[float],
    plan_t: Optional[float],
    wait_t: Optional[float],
    reuse_t: Optional[float],
) -> bool:
    pairs = [
        (to_float(row.get("rt_p95")), rt_t),
        (to_float(row.get("data_age_p95")), age_t),
        (to_float(row.get("planning_total_p95")), plan_t),
        (to_float(row.get("planning_wait_p95")), wait_t),
        (to_float(row.get("reuse_p95")), reuse_t),
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

    all_bins = sorted(set(latency_by_bin.keys()) | set(drop_counts.keys()))
    rows: List[Dict[str, object]] = []
    for idx, b in enumerate(all_bins):
        lat = latency_by_bin.get(b, {})
        drops = drop_counts.get(b, Counter())
        high_now = is_high_latency(lat, global_rt_threshold, global_age_threshold, global_plan_threshold, global_wait_threshold, global_reuse_threshold)
        next_lat = latency_by_bin.get(all_bins[idx + 1], {}) if idx + 1 < len(all_bins) else {}
        next_high = is_high_latency(next_lat, global_rt_threshold, global_age_threshold, global_plan_threshold, global_wait_threshold, global_reuse_threshold)
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
        rows.append({
            "time_bin_s": b,
            "drop_count_total": drops.get("total", 0),
            "drop_count_by_stage": stage_counts,
            "rt_p95": lat.get("rt_p95", ""),
            "data_age_p95": lat.get("data_age_p95", ""),
            "planning_total_p95": lat.get("planning_total_p95", ""),
            "planning_wait_p95": lat.get("planning_wait_p95", ""),
            "reuse_p95": lat.get("reuse_p95", ""),
            "corr_tag": corr_tag,
            "lead_lag_tag": lead_lag_tag,
        })
    return rows
