#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, pstdev
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from apollo_perf_trace_lib import (  # noqa: E402
    bin_seconds_for_ns,
    compute_run_start_ns,
    nonzero_int,
    percentile,
    read_csv_rows,
    to_float,
)

PALETTE = [
    "#0f766e",
    "#1d4ed8",
    "#b45309",
    "#be123c",
    "#6d28d9",
    "#374151",
    "#0ea5e9",
    "#65a30d",
]


def escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def load_run_start_ns(canonical_dir: Path) -> int:
    module_rows = read_csv_rows(canonical_dir / "module_phase_table.csv")
    handoff_rows = read_csv_rows(canonical_dir / "message_handoff_table.csv")
    e2e_rows = read_csv_rows(canonical_dir / "e2e_frame_table.csv")
    control_rows = read_csv_rows(canonical_dir / "control_usage_table.csv")
    return compute_run_start_ns(module_rows, handoff_rows, e2e_rows, control_rows)


def load_merged_timeline(canonical_dir: Path) -> Dict[int, Dict[str, float]]:
    merged: Dict[int, Dict[str, float]] = {}
    for fname in ("latency_timeline_table.csv", "latency_drop_alignment_table.csv"):
        path = canonical_dir / fname
        for row in read_csv_rows(path):
            t = int(float(row["time_bin_s"]))
            merged.setdefault(t, {})
            for k, v in row.items():
                if k in ("time_bin_s", "drop_count_by_stage", "corr_tag", "lead_lag_tag"):
                    continue
                fv = to_float(v)
                if fv is not None:
                    merged[t][k] = fv
    return merged


def filter_times(xs: Sequence[int], start_s: Optional[int], end_s: Optional[int]) -> List[int]:
    return [x for x in xs if (start_s is None or x >= start_s) and (end_s is None or x <= end_s)]


def series_from_metrics(rows: Dict[int, Dict[str, float]], metrics: Sequence[str], start_s: Optional[int], end_s: Optional[int]) -> Dict[str, List[Tuple[int, float]]]:
    xs = filter_times(sorted(rows.keys()), start_s, end_s)
    result: Dict[str, List[Tuple[int, float]]] = {}
    for metric in metrics:
        pts = [(t, rows[t].get(metric)) for t in xs if rows[t].get(metric) is not None]
        if pts:
            result[metric] = pts
    return result


def aggregate_stats(values: Sequence[float], stats: Sequence[str], deadline_ms: Optional[float]) -> Dict[str, float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {}
    p50 = percentile(clean, 50) or 0.0
    p95 = percentile(clean, 95) or 0.0
    result: Dict[str, float] = {}
    for stat in stats:
        if stat == "p50":
            result["p50"] = p50
        elif stat == "p95":
            result["p95"] = p95
        elif stat == "p99":
            result["p99"] = percentile(clean, 99) or 0.0
        elif stat == "max":
            result["max"] = max(clean)
        elif stat == "mean":
            result["mean"] = mean(clean)
        elif stat == "std":
            result["std"] = pstdev(clean) if len(clean) > 1 else 0.0
        elif stat == "jitter":
            result["jitter"] = p95 - p50
        elif stat == "miss_rate":
            if deadline_ms is None:
                raise ValueError("miss_rate requires --deadline-ms")
            result["miss_rate"] = 100.0 * sum(1 for v in clean if v > deadline_ms) / len(clean)
    return result


def build_module_series(canonical_dir: Path, run_start_ns: int, module: str, phase: str, stats: Sequence[str], bin_seconds: int, start_s: Optional[int], end_s: Optional[int], deadline_ms: Optional[float]) -> Dict[str, List[Tuple[int, float]]]:
    rows = read_csv_rows(canonical_dir / "module_phase_table.csv")
    bins: Dict[int, List[float]] = defaultdict(list)
    for row in rows:
        if row.get("module") != module or row.get("phase_label") != phase:
            continue
        anchor = nonzero_int(row.get("enter_ns"))
        lat = to_float(row.get("latency_ms"))
        if anchor and lat is not None:
            b = bin_seconds_for_ns(anchor, run_start_ns, bin_seconds)
            if (start_s is not None and b < start_s) or (end_s is not None and b > end_s):
                continue
            bins[b].append(lat)
    out: Dict[str, List[Tuple[int, float]]] = {stat: [] for stat in stats}
    for b in sorted(bins.keys()):
        agg = aggregate_stats(bins[b], stats, deadline_ms)
        for stat, value in agg.items():
            out.setdefault(stat, []).append((b, value))
    return {k: v for k, v in out.items() if v}


def build_sensor_fusion_series(canonical_dir: Path, run_start_ns: int, sensor_kind: Optional[str], sensor: Optional[str], stats: Sequence[str], bin_seconds: int, start_s: Optional[int], end_s: Optional[int], deadline_ms: Optional[float]) -> Dict[str, List[Tuple[int, float]]]:
    rows = read_csv_rows(canonical_dir / "trace_link_table.csv")
    bins: Dict[int, List[float]] = defaultdict(list)
    for row in rows:
        if sensor_kind and row.get("sensor_kind") != sensor_kind:
            continue
        if sensor and row.get("sensor") != sensor:
            continue
        origin = nonzero_int(row.get("sensor_origin_ns"))
        out_ns = nonzero_int(row.get("fusion_output_ns"))
        if not origin or not out_ns or out_ns < origin:
            continue
        latency_ms = (out_ns - origin) / 1e6
        b = bin_seconds_for_ns(origin, run_start_ns, bin_seconds)
        if (start_s is not None and b < start_s) or (end_s is not None and b > end_s):
            continue
        bins[b].append(latency_ms)
    out: Dict[str, List[Tuple[int, float]]] = {stat: [] for stat in stats}
    for b in sorted(bins.keys()):
        agg = aggregate_stats(bins[b], stats, deadline_ms)
        for stat, value in agg.items():
            out.setdefault(stat, []).append((b, value))
    return {k: v for k, v in out.items() if v}


def build_drop_stack_series(canonical_dir: Path, run_start_ns: int, bin_seconds: int, start_s: Optional[int], end_s: Optional[int], top_n: int) -> Dict[str, List[Tuple[int, float]]]:
    rows = read_csv_rows(canonical_dir / "drop_event_table.csv")
    counts_by_bin: Dict[int, Counter] = defaultdict(Counter)
    total_by_stage = Counter()
    for row in rows:
        break_ns = nonzero_int(row.get("break_ns"))
        if not break_ns:
            continue
        stage = row.get("break_stage") or "unknown"
        b = bin_seconds_for_ns(break_ns, run_start_ns, bin_seconds)
        if (start_s is not None and b < start_s) or (end_s is not None and b > end_s):
            continue
        counts_by_bin[b][stage] += 1
        total_by_stage[stage] += 1
    keep = [stage for stage, _ in total_by_stage.most_common(top_n)]
    series: Dict[str, List[Tuple[int, float]]] = {stage: [] for stage in keep}
    for b in sorted(counts_by_bin.keys()):
        others = 0
        for stage, cnt in counts_by_bin[b].items():
            if stage in keep:
                series[stage].append((b, float(cnt)))
            else:
                others += cnt
        if others:
            series.setdefault("other", []).append((b, float(others)))
    return {k: v for k, v in series.items() if v}


def line_chart_svg(series: Dict[str, List[Tuple[int, float]]], title: str, subtitle: str, y_label: str) -> str:
    if not series:
        raise ValueError("No series data to plot.")
    xs = sorted({x for pts in series.values() for x, _ in pts})
    ys = [y for pts in series.values() for _, y in pts]
    return generic_line_svg(series, xs, ys, title, subtitle, y_label, dual_right=None)


def dual_axis_svg(left: Dict[str, List[Tuple[int, float]]], right: Dict[str, List[Tuple[int, float]]], title: str, subtitle: str, left_label: str, right_label: str) -> str:
    if not left or not right:
        raise ValueError("Dual-axis chart requires both left and right series.")
    xs = sorted({x for pts in list(left.values()) + list(right.values()) for x, _ in pts})
    left_ys = [y for pts in left.values() for _, y in pts]
    right_ys = [y for pts in right.values() for _, y in pts]
    return generic_line_svg(left, xs, left_ys, title, subtitle, left_label, dual_right=(right, right_ys, right_label))


def generic_line_svg(
    left: Dict[str, List[Tuple[int, float]]],
    xs: List[int],
    left_ys: List[float],
    title: str,
    subtitle: str,
    left_label: str,
    dual_right: Optional[Tuple[Dict[str, List[Tuple[int, float]]], List[float], str]],
) -> str:
    width, height = 1280, 720
    ml, mr, mt, mb = 90, 90 if dual_right else 40, 70, 90
    pw, ph = width - ml - mr, height - mt - mb
    x_min, x_max = min(xs), max(xs) if len(xs) > 1 else min(xs) + 1
    y_min, y_max = scaled_bounds(left_ys)

    def sx(x: float) -> float:
        return ml + (x - x_min) / (x_max - x_min) * pw

    def sy(y: float) -> float:
        return mt + (y_max - y) / (y_max - y_min) * ph

    right = None
    right_ymin = right_ymax = None
    if dual_right:
        right, right_ys, _right_label = dual_right
        right_ymin, right_ymax = scaled_bounds(right_ys)

        def sy_right(y: float) -> float:
            return mt + (right_ymax - y) / (right_ymax - right_ymin) * ph
    else:
        sy_right = None

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text x="{ml}" y="38" font-size="26" font-family="Segoe UI, Arial, sans-serif" fill="#0f172a">{escape(title)}</text>',
        f'<text x="{ml}" y="58" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#475569">{escape(subtitle)}</text>',
    ]

    add_y_grid(parts, ml, mt, pw, ph, y_min, y_max, sy, align="left")
    if dual_right:
        add_y_labels_only(parts, ml + pw + 12, right_ymin, right_ymax, sy_right)  # type: ignore[arg-type]

    tick_candidates = xs if len(xs) <= 10 else [xs[int(i * (len(xs) - 1) / 9)] for i in range(10)]
    tick_candidates = sorted(set(tick_candidates))
    for t in tick_candidates:
        px = sx(t)
        parts.append(f'<line x1="{px:.1f}" y1="{mt}" x2="{px:.1f}" y2="{mt + ph}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(f'<text x="{px:.1f}" y="{mt + ph + 24}" text-anchor="middle" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{t}s</text>')
    parts.append(f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#0f172a" stroke-width="1.5"/>')
    parts.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#0f172a" stroke-width="1.5"/>')
    if dual_right:
        parts.append(f'<line x1="{ml + pw}" y1="{mt}" x2="{ml + pw}" y2="{mt + ph}" stroke="#0f172a" stroke-width="1.2"/>')

    legend_items: List[Tuple[str, str, bool]] = []
    color_idx = 0
    for name, pts in left.items():
        color = PALETTE[color_idx % len(PALETTE)]
        color_idx += 1
        path = " ".join(("M" if i == 0 else "L") + f" {sx(x):.1f} {sy(y):.1f}" for i, (x, y) in enumerate(pts))
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        legend_items.append((name, color, False))
    if dual_right and right:
        for name, pts in right.items():
            color = PALETTE[color_idx % len(PALETTE)]
            color_idx += 1
            path = " ".join(("M" if i == 0 else "L") + f" {sx(x):.1f} {sy_right(y):.1f}" for i, (x, y) in enumerate(pts))  # type: ignore[misc]
            parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.5" stroke-dasharray="7 5"/>')
            legend_items.append((name + " (right)", color, True))

    draw_legend(parts, legend_items, height)
    parts.append(f'<text x="{ml + pw/2:.1f}" y="{height - 40}" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#334155">relative time (s)</text>')
    parts.append(f'<text x="24" y="{mt + ph/2:.1f}" transform="rotate(-90 24 {mt + ph/2:.1f})" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{escape(left_label)}</text>')
    if dual_right:
        _, _, right_label = dual_right
        parts.append(f'<text x="{width - 24}" y="{mt + ph/2:.1f}" transform="rotate(90 {width - 24} {mt + ph/2:.1f})" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{escape(right_label)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def stacked_bar_svg(series: Dict[str, List[Tuple[int, float]]], title: str, subtitle: str, y_label: str) -> str:
    if not series:
        raise ValueError("No stacked-bar data to plot.")
    width, height = 1280, 720
    ml, mr, mt, mb = 90, 40, 70, 90
    pw, ph = width - ml - mr, height - mt - mb
    xs = sorted({x for pts in series.values() for x, _ in pts})
    stage_map = {name: dict(pts) for name, pts in series.items()}
    totals = [sum(stage_map[name].get(x, 0.0) for name in stage_map) for x in xs]
    y_min, y_max = 0.0, max(totals) if totals else 1.0
    if y_max <= 0:
        y_max = 1.0
    y_max *= 1.12
    x_min, x_max = min(xs), max(xs) if len(xs) > 1 else min(xs) + 1
    bar_w = max(8.0, pw / max(len(xs), 1) * 0.7)

    def sx(x: float) -> float:
        return ml + (x - x_min) / (x_max - x_min) * pw

    def sy(y: float) -> float:
        return mt + (y_max - y) / (y_max - y_min) * ph

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text x="{ml}" y="38" font-size="26" font-family="Segoe UI, Arial, sans-serif" fill="#0f172a">{escape(title)}</text>',
        f'<text x="{ml}" y="58" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#475569">{escape(subtitle)}</text>',
    ]
    add_y_grid(parts, ml, mt, pw, ph, y_min, y_max, sy, align="left")
    tick_candidates = xs if len(xs) <= 10 else [xs[int(i * (len(xs) - 1) / 9)] for i in range(10)]
    tick_candidates = sorted(set(tick_candidates))
    for t in tick_candidates:
        px = sx(t)
        parts.append(f'<line x1="{px:.1f}" y1="{mt}" x2="{px:.1f}" y2="{mt + ph}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(f'<text x="{px:.1f}" y="{mt + ph + 24}" text-anchor="middle" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{t}s</text>')
    parts.append(f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#0f172a" stroke-width="1.5"/>')
    parts.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#0f172a" stroke-width="1.5"/>')

    legend_items: List[Tuple[str, str, bool]] = []
    for idx, name in enumerate(series.keys()):
        color = PALETTE[idx % len(PALETTE)]
        legend_items.append((name, color, False))
        for x in xs:
            base = sum(stage_map[n].get(x, 0.0) for n in list(series.keys())[:idx])
            val = stage_map[name].get(x, 0.0)
            if val <= 0:
                continue
            x0 = sx(x) - bar_w / 2
            y0 = sy(base + val)
            h = sy(base) - sy(base + val)
            parts.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{color}" opacity="0.88"/>')

    draw_legend(parts, legend_items, height)
    parts.append(f'<text x="{ml + pw/2:.1f}" y="{height - 40}" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#334155">relative time (s)</text>')
    parts.append(f'<text x="24" y="{mt + ph/2:.1f}" transform="rotate(-90 24 {mt + ph/2:.1f})" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{escape(y_label)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def scaled_bounds(values: Sequence[float]) -> Tuple[float, float]:
    y_min = min(values)
    y_max = max(values)
    if math.isclose(y_min, y_max):
        y_min -= 1.0
        y_max += 1.0
    pad = (y_max - y_min) * 0.08
    return y_min - pad, y_max + pad


def add_y_grid(parts: List[str], ml: int, mt: int, pw: int, ph: int, y_min: float, y_max: float, sy, align: str) -> None:
    for i in range(6):
        frac = i / 5
        yv = y_min + (y_max - y_min) * frac
        py = sy(yv)
        parts.append(f'<line x1="{ml}" y1="{py:.1f}" x2="{ml + pw}" y2="{py:.1f}" stroke="#cbd5e1" stroke-width="1"/>')
        if align == "left":
            parts.append(f'<text x="{ml - 12}" y="{py + 4:.1f}" text-anchor="end" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{yv:.2f}</text>')


def add_y_labels_only(parts: List[str], x_text: float, y_min: float, y_max: float, sy) -> None:
    for i in range(6):
        frac = i / 5
        yv = y_min + (y_max - y_min) * frac
        py = sy(yv)
        parts.append(f'<text x="{x_text:.1f}" y="{py + 4:.1f}" text-anchor="start" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{yv:.2f}</text>')


def draw_legend(parts: List[str], items: Sequence[Tuple[str, str, bool]], height: int) -> None:
    legend_x = 100
    legend_y = height - 24
    for idx, (label, color, dashed) in enumerate(items):
        lx = legend_x + idx * 210
        dash_attr = ' stroke-dasharray="7 5"' if dashed else ""
        parts.append(f'<line x1="{lx}" y1="{legend_y}" x2="{lx + 24}" y2="{legend_y}" stroke="{color}" stroke-width="3"{dash_attr}/>')
        parts.append(f'<text x="{lx + 32}" y="{legend_y + 4}" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#0f172a">{escape(label)}</text>')


def default_out(canonical_dir: Path, prefix: str) -> Path:
    out = canonical_dir / "plots" / f"{prefix}.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def cmd_timeline(args) -> Path:
    rows = load_merged_timeline(args.canonical_dir)
    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    series = series_from_metrics(rows, metrics, args.start_s, args.end_s)
    svg = line_chart_svg(series, args.title or "Latency Timeline", args.subtitle or "Merged canonical timeline metrics", "metric value")
    out = Path(args.out).resolve() if args.out else default_out(args.canonical_dir, "_".join(metrics[:4]) or "timeline")
    out.write_text(svg, encoding="utf-8")
    return out


def cmd_dual_axis(args) -> Path:
    rows = load_merged_timeline(args.canonical_dir)
    left = series_from_metrics(rows, [m.strip() for m in args.left_metrics.split(",") if m.strip()], args.start_s, args.end_s)
    right = series_from_metrics(rows, [m.strip() for m in args.right_metrics.split(",") if m.strip()], args.start_s, args.end_s)
    svg = dual_axis_svg(left, right, args.title or "Dual-Axis Timeline", args.subtitle or "Left and right timeline metrics", args.left_label or "left metric", args.right_label or "right metric")
    out = Path(args.out).resolve() if args.out else default_out(args.canonical_dir, f"dual_{safe(args.left_metrics)}__{safe(args.right_metrics)}")
    out.write_text(svg, encoding="utf-8")
    return out


def cmd_module(args) -> Path:
    run_start = load_run_start_ns(args.canonical_dir)
    stats = [s.strip() for s in args.stats.split(",") if s.strip()]
    series = build_module_series(args.canonical_dir, run_start, args.module, args.phase, stats, args.bin_seconds, args.start_s, args.end_s, args.deadline_ms)
    title = args.title or f"{args.module}:{args.phase} timeline"
    subtitle = args.subtitle or f"stats={','.join(stats)}; bin={args.bin_seconds}s"
    svg = line_chart_svg(series, title, subtitle, "ms or %")
    deadline_part = f"_deadline_{int(args.deadline_ms)}" if args.deadline_ms is not None else ""
    out = Path(args.out).resolve() if args.out else default_out(args.canonical_dir, f"module_{safe(args.module)}_{safe(args.phase)}_{safe('_'.join(stats))}{deadline_part}")
    out.write_text(svg, encoding="utf-8")
    return out


def cmd_sensor_fusion(args) -> Path:
    run_start = load_run_start_ns(args.canonical_dir)
    stats = [s.strip() for s in args.stats.split(",") if s.strip()]
    series = build_sensor_fusion_series(args.canonical_dir, run_start, args.sensor_kind, args.sensor, stats, args.bin_seconds, args.start_s, args.end_s, args.deadline_ms)
    title = args.title or f"sensor→fusion timeline"
    subtitle = args.subtitle or f"sensor_kind={args.sensor_kind or 'all'}; sensor={args.sensor or 'all'}; stats={','.join(stats)}"
    svg = line_chart_svg(series, title, subtitle, "ms or %")
    deadline_part = f"_deadline_{int(args.deadline_ms)}" if args.deadline_ms is not None else ""
    out = Path(args.out).resolve() if args.out else default_out(args.canonical_dir, f"sensor_fusion_{safe(args.sensor_kind or args.sensor or 'all')}_{safe('_'.join(stats))}{deadline_part}")
    out.write_text(svg, encoding="utf-8")
    return out


def cmd_drop_stack(args) -> Path:
    run_start = load_run_start_ns(args.canonical_dir)
    series = build_drop_stack_series(args.canonical_dir, run_start, args.bin_seconds, args.start_s, args.end_s, args.top_n)
    title = args.title or "Drop counts by break stage"
    subtitle = args.subtitle or f"top {args.top_n} break stages; bin={args.bin_seconds}s"
    svg = stacked_bar_svg(series, title, subtitle, "drop count")
    out = Path(args.out).resolve() if args.out else default_out(args.canonical_dir, f"drop_stack_top_{args.top_n}")
    out.write_text(svg, encoding="utf-8")
    return out


def cmd_standard_suite(args) -> Path:
    rows = load_merged_timeline(args.canonical_dir)
    run_start = load_run_start_ns(args.canonical_dir)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else args.canonical_dir / "plots" / "standard_suite"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    def write_chart(name: str, builder) -> None:
        out = out_dir / f"{name}.svg"
        try:
            svg = builder()
            out.write_text(svg, encoding="utf-8")
            manifest.append({"name": name, "path": str(out), "status": "ok"})
        except Exception as exc:
            manifest.append({"name": name, "path": str(out), "status": "skipped", "reason": str(exc)})

    write_chart(
        "rt_p50_p95_p99",
        lambda: line_chart_svg(
            series_from_metrics(rows, ("rt_p50", "rt_p95", "rt_p99"), args.start_s, args.end_s),
            "RT percentile timeline",
            "p50/p95/p99 by time window",
            "RT (ms)",
        ),
    )
    write_chart(
        "data_age_p50_p95_p99",
        lambda: line_chart_svg(
            series_from_metrics(rows, ("data_age_p50", "data_age_p95", "data_age_p99"), args.start_s, args.end_s),
            "Data Age percentile timeline",
            "p50/p95/p99 by time window",
            "Data Age (ms)",
        ),
    )
    write_chart(
        "drop_vs_rt_p95",
        lambda: dual_axis_svg(
            series_from_metrics(rows, ("drop_count_total",), args.start_s, args.end_s),
            series_from_metrics(rows, ("rt_p95",), args.start_s, args.end_s),
            "Drop count vs RT P95",
            "Check whether drop bursts align with high RT windows",
            "drop count",
            "RT P95 (ms)",
        ),
    )
    write_chart(
        "drop_vs_data_age_p95",
        lambda: dual_axis_svg(
            series_from_metrics(rows, ("drop_count_total",), args.start_s, args.end_s),
            series_from_metrics(rows, ("data_age_p95",), args.start_s, args.end_s),
            "Drop count vs Data Age P95",
            "Check whether drop bursts align with stale-data windows",
            "drop count",
            "Data Age P95 (ms)",
        ),
    )
    write_chart(
        "rt_miss_rate_vs_rt_p99",
        lambda: dual_axis_svg(
            series_from_metrics(rows, ("rt_miss_rate_pct",), args.start_s, args.end_s),
            series_from_metrics(rows, ("rt_p99",), args.start_s, args.end_s),
            "RT miss rate vs RT P99",
            "Required check for tail-driven RT deadline misses",
            "RT miss rate (%)",
            "RT P99 (ms)",
        ),
    )
    write_chart(
        "data_age_miss_rate_vs_data_age_p99",
        lambda: dual_axis_svg(
            series_from_metrics(rows, ("data_age_miss_rate_pct",), args.start_s, args.end_s),
            series_from_metrics(rows, ("data_age_p99",), args.start_s, args.end_s),
            "Data Age miss rate vs Data Age P99",
            "Required check for tail-driven Data Age deadline misses",
            "Data Age miss rate (%)",
            "Data Age P99 (ms)",
        ),
    )
    write_chart(
        "drop_count_by_break_stage",
        lambda: stacked_bar_svg(
            build_drop_stack_series(args.canonical_dir, run_start, args.bin_seconds, args.start_s, args.end_s, args.top_n),
            "Drop counts by break stage",
            f"top {args.top_n} break stages; bin={args.bin_seconds}s",
            "drop count",
        ),
    )

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def safe(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a richer suite of SVG charts from Apollo perf trace canonical tables.")
    parser.add_argument("canonical_dir", type=Path, help="Canonical output directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("timeline", help="Line chart from merged latency/alignment timeline metrics")
    p.add_argument("--metrics", required=True, help="Comma-separated metrics, e.g. rt_p95,data_age_p95")
    p.add_argument("--start-s", type=int)
    p.add_argument("--end-s", type=int)
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--out")
    p.set_defaults(func=cmd_timeline)

    p = sub.add_parser("dual-axis", help="Dual-axis chart from merged timeline metrics")
    p.add_argument("--left-metrics", required=True)
    p.add_argument("--right-metrics", required=True)
    p.add_argument("--start-s", type=int)
    p.add_argument("--end-s", type=int)
    p.add_argument("--left-label")
    p.add_argument("--right-label")
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--out")
    p.set_defaults(func=cmd_dual_axis)

    p = sub.add_parser("module", help="Module or phase latency timeline, jitter, and miss-rate charts")
    p.add_argument("--module", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--stats", default="p95,jitter")
    p.add_argument("--deadline-ms", type=float)
    p.add_argument("--bin-seconds", type=int, default=1)
    p.add_argument("--start-s", type=int)
    p.add_argument("--end-s", type=int)
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--out")
    p.set_defaults(func=cmd_module)

    p = sub.add_parser("sensor-fusion", help="sensor→fusion latency timeline charts")
    p.add_argument("--sensor-kind")
    p.add_argument("--sensor")
    p.add_argument("--stats", default="p95,mean")
    p.add_argument("--deadline-ms", type=float)
    p.add_argument("--bin-seconds", type=int, default=1)
    p.add_argument("--start-s", type=int)
    p.add_argument("--end-s", type=int)
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--out")
    p.set_defaults(func=cmd_sensor_fusion)

    p = sub.add_parser("drop-stack", help="Stacked drop-count chart by break stage")
    p.add_argument("--top-n", type=int, default=6)
    p.add_argument("--bin-seconds", type=int, default=1)
    p.add_argument("--start-s", type=int)
    p.add_argument("--end-s", type=int)
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--out")
    p.set_defaults(func=cmd_drop_stack)

    p = sub.add_parser("standard-suite", help="Render the minimum standard report chart suite")
    p.add_argument("--bin-seconds", type=int, default=1)
    p.add_argument("--start-s", type=int)
    p.add_argument("--end-s", type=int)
    p.add_argument("--top-n", type=int, default=6)
    p.add_argument("--out-dir")
    p.set_defaults(func=cmd_standard_suite)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.canonical_dir = args.canonical_dir.resolve()
    out = args.func(args)
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
