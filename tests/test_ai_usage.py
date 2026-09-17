"""Offline AI cost/unit metering. No provider HTTP or Stripe calls."""
import json
import os
import unittest
from unittest.mock import patch

from jobagent.mobile.ai_usage import (
    attach_packet_burn, estimate_cost_usd, packet_burn_from_runs, request_type_for,
    summarize_packet_burn, usage_record,
)


class AIUsageTests(unittest.TestCase):
    def test_request_types_and_unknown_operations(self):
        self.assertEqual(request_type_for("rank_job"), "assessment")
        self.assertEqual(request_type_for("prepare_documents"), "packet_prepare")
        self.assertEqual(request_type_for("answer_chat"), "chat")
        self.assertIsNone(request_type_for("erase"))

    def test_known_model_cost_and_unknown_uses_conservative_ceiling(self):
        cheap = estimate_cost_usd("gpt-4.1", 1_000_000, 1_000_000)
        unknown = estimate_cost_usd("mystery-model", 1_000_000, 1_000_000)
        self.assertEqual(cheap, 10.0)
        self.assertGreater(unknown, cheap)

    def test_usage_record_drops_secrets_and_non_integers(self):
        record = usage_record({
            "provider": "openai", "model_name": "gpt-4.1", "input_tokens": 100,
            "output_tokens": 50, "secret": "sk-never", "prompt": "resume text",
        }, operation="rank_job", reserved_units=1, reservation_id="11111111-1111-4111-8111-111111111111")
        dumped = json.dumps(record)
        self.assertNotIn("sk-never", dumped)
        self.assertNotIn("resume", dumped)
        self.assertEqual(record["request_type"], "assessment")
        self.assertEqual(record["reserved_units"], 1)
        self.assertEqual(record["input_tokens"], 100)
        self.assertIn("estimated_cost_usd", record)

    def test_invalid_tokens_and_ids_are_omitted(self):
        record = usage_record({"input_tokens": -1, "output_tokens": "12"}, reserved_units=True,
                              reservation_id="not-a-uuid")
        self.assertEqual(record, {})

    def test_completed_packet_means_rank_plus_prepare(self):
        user, job = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        runs = [
            {"id": "rank-1", "user_id": user, "job_id": job, "operation": "rank_job", "status": "succeeded",
             "completed_at": "2026-09-08T10:00:00Z",
             "output_summary": {"usage": {"request_type": "assessment", "input_tokens": 10, "output_tokens": 5,
                                          "estimated_cost_usd": 0.01, "reserved_units": 1}}},
            {"id": "prep-1", "user_id": user, "job_id": job, "operation": "prepare_documents", "status": "succeeded",
             "completed_at": "2026-09-08T11:00:00Z",
             "output_summary": {"usage": {"request_type": "packet_prepare", "input_tokens": 20, "output_tokens": 40,
                                          "estimated_cost_usd": 0.04, "reserved_units": 2}}},
            {"id": "prep-2", "user_id": user, "job_id": "33333333-3333-4333-8333-333333333333",
             "operation": "prepare_documents", "status": "succeeded",
             "completed_at": "2026-09-08T12:00:00Z",
             "output_summary": {"model_metadata": {"input_tokens": 1, "output_tokens": 1}}},
        ]
        packets = packet_burn_from_runs(runs)
        self.assertEqual(len(packets), 2)
        complete = packets[0]
        self.assertFalse(complete["incomplete"])
        self.assertEqual(complete["assessment_run_id"], "rank-1")
        self.assertEqual(complete["reserved_units"], 3)
        self.assertAlmostEqual(complete["estimated_cost_usd"], 0.05)
        self.assertTrue(packets[1]["incomplete"])
        self.assertIsNone(packets[1]["assessment_run_id"])
        summary = summarize_packet_burn(packets)
        self.assertEqual(summary["completed_packets"], 1)
        self.assertEqual(summary["incomplete_packets"], 1)
        self.assertEqual(summary["mean_reserved_units"], 3)
        self.assertAlmostEqual(summary["mean_estimated_cost_usd"], 0.05)

    def test_price_table_override_rejects_secrets_shaped_keys(self):
        with patch.dict(os.environ, {"MOBILE_AI_PRICE_TABLE": '{"sk-secret": {"input": 1, "output": 1}}'}):
            self.assertEqual(estimate_cost_usd("sk-secret", 1_000_000, 0), 15.0)

    def test_attach_packet_burn_without_assessment_is_incomplete(self):
        packet = attach_packet_burn({
            "id": "prep", "user_id": "u", "job_id": "j", "operation": "prepare_documents",
            "status": "succeeded", "completed_at": "2026-09-08T11:00:00Z",
            "output_summary": {"usage": {"reserved_units": 2, "estimated_cost_usd": 0.02,
                                         "input_tokens": 1, "output_tokens": 1}},
        }, None)
        self.assertTrue(packet["incomplete"])
        self.assertEqual(packet["prepare_run_id"], "prep")


if __name__ == "__main__":
    unittest.main()
