#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DEFAULT_COLORS = [
    "#0f766e",
    "#1d4ed8",
    "#b45309",
    "#be123c",
    "#6d28d9",
    "#374151",
]


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_float(value: object) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def load_merged_rows(canonical_dir: Path) -> Dict[int, Dict[str, float]]:
    timeline_path = canonical_dir / "latency_timeline_table.csv"
    alignment_path = canonical_dir / "latency_drop_alignment_table.csv"
    if not timeline_path.exists() or not alignment_path.exists():
        raise FileNotFoundError("canonical_dir must contain latency_timeline_table.csv and latency_drop_alignment_table.csv")

    merged: Dict[int, Dict[str, float]] = {}
    for row in read_csv_rows(timeline_path):
        t = int(float(row["time_bin_s"]))
        merged.setdefault(t, {})
        for k, v in row.items():
            if k == "time_bin_s":
                continue
            fv = to_float(v)
            if fv is not None:
                merged[t][k] = fv
    for row in read_csv_rows(alignment_path):
        t = int(float(row["time_bin_s"]))
        merged.setdefault(t, {})
        for k, v in row.items():
            if k in ("time_bin_s", "drop_count_by_stage", "corr_tag", "lead_lag_tag"):
                continue
            fv = to_float(v)
            if fv is not None:
                merged[t][k] = fv
    return merged


def build_svg(
    rows: Dict[int, Dict[str, float]],
    metrics: List[str],
    start_s: Optional[int],
    end_s: Optional[int],
    title: str,
) -> str:
    xs = sorted(t for t in rows.keys() if (start_s is None or t >= start_s) and (end_s is None or t <= end_s))
    if not xs:
        raise ValueError("No data points found in the requested time range.")

    series = []
    for metric in metrics:
        pts = [(t, rows[t].get(metric)) for t in xs if rows[t].get(metric) is not None]
        if pts:
            series.append((metric, pts))
    if not series:
        raise ValueError("None of the requested metrics had data in the requested time range.")

    all_y = [y for _, pts in series for _, y in pts]
    y_min = min(all_y)
    y_max = max(all_y)
    if math.isclose(y_min, y_max):
        y_min -= 1.0
        y_max += 1.0
    pad = (y_max - y_min) * 0.08
    y_min -= pad
    y_max += pad

    width = 1280
    height = 720
    margin_left = 90
    margin_right = 40
    margin_top = 70
    margin_bottom = 80
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    x_min = min(xs)
    x_max = max(xs)
    if x_min == x_max:
        x_max += 1

    def sx(x: float) -> float:
        return margin_left + (x - x_min) / (x_max - x_min) * plot_w

    def sy(y: float) -> float:
        return margin_top + (y_max - y) / (y_max - y_min) * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text x="{margin_left}" y="38" font-size="26" font-family="Segoe UI, Arial, sans-serif" fill="#0f172a">{escape(title)}</text>',
        f'<text x="{margin_left}" y="58" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#475569">Time window: {x_min}s to {x_max}s</text>',
    ]

    for i in range(6):
        frac = i / 5
        yv = y_min + (y_max - y_min) * frac
        py = sy(yv)
        parts.append(f'<line x1="{margin_left}" y1="{py:.1f}" x2="{margin_left + plot_w}" y2="{py:.1f}" stroke="#cbd5e1" stroke-width="1"/>')
        parts.append(f'<text x="{margin_left - 12}" y="{py + 4:.1f}" text-anchor="end" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{yv:.2f}</text>')

    tick_candidates = xs if len(xs) <= 10 else [xs[int(i * (len(xs) - 1) / 9)] for i in range(10)]
    tick_candidates = sorted(set(tick_candidates))
    for t in tick_candidates:
        px = sx(t)
        parts.append(f'<line x1="{px:.1f}" y1="{margin_top}" x2="{px:.1f}" y2="{margin_top + plot_h}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(f'<text x="{px:.1f}" y="{margin_top + plot_h + 24}" text-anchor="middle" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#334155">{t}s</text>')

    parts.append(f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" y2="{margin_top + plot_h}" stroke="#0f172a" stroke-width="1.5"/>')
    parts.append(f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#0f172a" stroke-width="1.5"/>')

    legend_x = margin_left + 10
    legend_y = height - 26
    for idx, (metric, pts) in enumerate(series):
        color = DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]
        path = " ".join(
            ("M" if i == 0 else "L") + f" {sx(x):.1f} {sy(y):.1f}"
            for i, (x, y) in enumerate(pts)
        )
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        lx = legend_x + idx * 220
        parts.append(f'<line x1="{lx}" y1="{legend_y}" x2="{lx + 24}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{lx + 32}" y="{legend_y + 4}" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#0f172a">{escape(metric)}</text>')

    parts.append(f'<text x="{margin_left + plot_w/2:.1f}" y="{height - 40}" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#334155">relative time (s)</text>')
    parts.append(f'<text x="24" y="{margin_top + plot_h/2:.1f}" transform="rotate(-90 24 {margin_top + plot_h/2:.1f})" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#334155">metric value</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a simple SVG chart from canonical timeline tables.")
    parser.add_argument("canonical_dir", help="Canonical output directory")
    parser.add_argument("--metrics", required=True, help="Comma-separated metric names, e.g. rt_p95,data_age_p95")
    parser.add_argument("--start-s", type=int, help="Start second in relative run time")
    parser.add_argument("--end-s", type=int, help="End second in relative run time")
    parser.add_argument("--title", default="Apollo Perf Trace Metrics", help="Chart title")
    parser.add_argument("--out", help="Output SVG path; defaults to <canonical_dir>/plots/<metrics>.svg")
    args = parser.parse_args()

    canonical_dir = Path(args.canonical_dir).resolve()
    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    rows = load_merged_rows(canonical_dir)

    default_name = "_".join(metrics[:4]) or "metrics"
    out_path = Path(args.out).resolve() if args.out else canonical_dir / "plots" / f"{default_name}.svg"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    svg = build_svg(rows, metrics, args.start_s, args.end_s, args.title)
    out_path.write_text(svg, encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
