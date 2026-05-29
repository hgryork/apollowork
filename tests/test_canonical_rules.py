from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from apollo_perf_trace_lib import build_deadline_metrics, build_drop_events, infer_deadline_config  # noqa: E402


def row(**kwargs):
    return {key: str(value) for key, value in kwargs.items()}


class CanonicalRuleTests(unittest.TestCase):
    def test_high_reuse_without_newer_planning_is_not_soft_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            e2e_rows = [
                row(
                    fusion_trace_id="old",
                    sensor_origin_ns=1_000_000,
                    fusion_output_ns=2_000_000,
                    planning_output_ns=10_000_000,
                    first_control_consume_ns=12_000_000,
                    complete_path=1,
                )
            ]
            control_rows = [
                row(
                    fusion_trace_id="old",
                    first_control_consume_ns=12_000_000,
                    first_control_output_ns=13_000_000,
                    last_control_output_ns=90_000_000,
                    control_reuse_count=8,
                )
            ]

            drops = build_drop_events(Path(tmp), [], [], e2e_rows, control_rows)

        self.assertFalse(any(drop["drop_type"] == "soft_drop" for drop in drops))

    def test_newer_planning_not_consumed_creates_soft_drop_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            e2e_rows = [
                row(
                    fusion_trace_id="old",
                    sensor_origin_ns=1_000_000,
                    fusion_output_ns=2_000_000,
                    planning_output_ns=10_000_000,
                    first_control_consume_ns=12_000_000,
                    complete_path=1,
                ),
                row(
                    fusion_trace_id="new",
                    sensor_origin_ns=20_000_000,
                    fusion_output_ns=21_000_000,
                    planning_output_ns=30_000_000,
                    first_control_consume_ns=0,
                    complete_path=0,
                    missing_stage="first_control_consume",
                ),
            ]
            control_rows = [
                row(
                    fusion_trace_id="old",
                    first_control_consume_ns=12_000_000,
                    first_control_output_ns=13_000_000,
                    last_control_output_ns=90_000_000,
                    control_reuse_count=8,
                )
            ]

            drops = build_drop_events(Path(tmp), [], [], e2e_rows, control_rows)

        soft_types = {drop["evidence_type"] for drop in drops if drop["drop_type"] == "soft_drop"}
        self.assertIn("unused_by_control", soft_types)
        self.assertIn("stale_reuse", soft_types)

    def test_deadline_metrics_keep_eligible_and_miss_counts(self):
        module_rows = [
            row(module="planning", phase_label="total", enter_ns=1_000, latency_ms=50),
            row(module="planning", phase_label="total", enter_ns=2_000, latency_ms=100),
        ]
        handoff_rows = [
            row(edge_name="planning_to_control", mono_ns_src=1_000, handoff_ms=10),
            row(edge_name="planning_to_control", mono_ns_src=2_000, handoff_ms=20),
        ]
        e2e_rows = [
            row(complete_path=1, sensor_origin_ns=1_000, reaction_time_ms=100),
            row(complete_path=1, sensor_origin_ns=2_000, reaction_time_ms=200),
        ]

        deadlines = {
            "planning_total_deadline": {"threshold_ms": 80.0, "threshold_source": "test", "base_period_ms": ""},
            "planning_to_control_deadline": {"threshold_ms": 15.0, "threshold_source": "test", "base_period_ms": ""},
            "e2e_rt_deadline": {"threshold_ms": 150.0, "threshold_source": "test", "base_period_ms": ""},
        }
        metrics = build_deadline_metrics(module_rows, handoff_rows, e2e_rows, run_start_ns=1_000, steady_start_s=None, deadline_config=deadlines)
        raw = {row["metric_name"]: row for row in metrics if row["scope"] == "raw"}

        self.assertEqual(raw["planning_total_deadline"]["eligible_count"], 2)
        self.assertEqual(raw["planning_total_deadline"]["miss_count"], 1)
        self.assertEqual(raw["planning_to_control_deadline"]["miss_count"], 1)
        self.assertEqual(raw["e2e_rt_deadline"]["miss_count"], 1)

    def test_deadlines_are_inferred_from_planning_period_and_can_be_overridden(self):
        module_rows = [
            row(module="planning", phase_label="total", enter_ns=1_000_000),
            row(module="planning", phase_label="total", enter_ns=51_000_000),
            row(module="planning", phase_label="total", enter_ns=101_000_000),
        ]
        config = infer_deadline_config(module_rows, [], {"planning_total_deadline": 42.0})

        self.assertEqual(config["planning_total_deadline"]["threshold_ms"], 42.0)
        self.assertEqual(config["planning_total_deadline"]["threshold_source"], "override")
        self.assertEqual(config["planning_to_control_deadline"]["threshold_ms"], 10.0)
        self.assertEqual(config["e2e_rt_deadline"]["threshold_ms"], 100.0)


if __name__ == "__main__":
    unittest.main()
