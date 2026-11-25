"""Unit tests for Antigravity CLI 2-line statusline HUD."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import statusline as sl

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 23, 22, 41, 0, tzinfo=TZ)


def make_transcript(iso_ts: str, model: str = "gemini-4-neon-xhigh") -> str:
    tf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    tf.write(
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "created_at": iso_ts,
                "model": model,
                "input_tokens": 5000,
                "cache_read_tokens": 168000,
            }
        )
        + "\n"
    )
    tf.close()
    return tf.name


def make_compaction_transcript(checkpoint_ts: str, summary_tokens: int = 4287, next_turn_ts: str | None = None) -> str:
    tf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    tf.write(
        json.dumps(
            {
                "step_index": 801,
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-23T12:00:00Z",
                "model": "gemini-4-carbon-high",
                "input_tokens": 2121,
                "cache_read_tokens": 462655,
                "output_tokens": 3619,
            }
        )
        + "\n"
    )
    tf.write(
        json.dumps(
            {
                "step_index": 804,
                "type": "CHECKPOINT",
                "created_at": checkpoint_ts,
                "input_tokens": 469427,
                "cache_read_tokens": 0,
                "output_tokens": summary_tokens,
                "content": "# Resuming from a compaction",
            }
        )
        + "\n"
    )
    if next_turn_ts:
        tf.write(
            json.dumps(
                {
                    "step_index": 806,
                    "type": "PLANNER_RESPONSE",
                    "created_at": next_turn_ts,
                    "model": "gemini-4-carbon-high",
                    "input_tokens": 17983,
                    "cache_read_tokens": 0,
                    "output_tokens": 689,
                }
            )
            + "\n"
        )
    tf.close()
    return tf.name


class TestStatusline(unittest.TestCase):
    def setUp(self) -> None:
        self.t_recent = make_transcript("2026-11-23T14:39:00Z")  # 22:39 +08:00
        self.t_old = make_transcript("2026-11-23T12:00:00Z")  # 20:00 +08:00
        self.quota = {
            "gemini-5h": {"remaining_fraction": 0.88, "reset_time": "2026-11-23T16:24:30Z"},
            "gemini-weekly": {"remaining_fraction": 0.85, "reset_time": "2026-11-29T07:36:48Z"},
            "3p-5h": {"remaining_fraction": 0.40, "reset_time": "2026-11-23T18:10:00Z"},
            "3p-weekly": {"remaining_fraction": 0.12, "reset_time": "2026-11-30T14:00:00Z"},
        }

    def tearDown(self) -> None:
        for p in (self.t_recent, self.t_old):
            if os.path.exists(p):
                os.remove(p)

    def _plain_lines(self, payload: dict) -> list[str]:
        return [sl.ANSI_RE.sub("", ln) for ln in sl.render(payload, now_local=NOW).splitlines()]

    def test_neon_two_lines_full(self) -> None:
        lines = self._plain_lines(
            {
                "model": {"id": "Gemini 4 Neon (XHigh)"},
                "transcript_path": self.t_recent,
                "terminal_width": 100,
                "quota": self.quota,
                "context_window": {
                    "context_window_size": 1048576,
                    "total_input_tokens": 185000,
                    "used_percentage": 17.64,
                    "current_usage": {
                        "input_tokens": 8000,
                        "cache_read_input_tokens": 168000,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 900,
                    },
                },
            }
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("17.6% (185k/1M)", lines[0])
        self.assertIn("缓存 95% (168k) 约23:39过期", lines[0])
        self.assertEqual(lines[1], "限额(Gemini) 5h剩88% (1时44分后重置) │ 周剩85% (5天16时后重置)")

    def test_claude_exact_ttl_and_3p_pool(self) -> None:
        lines = self._plain_lines(
            {
                "model": {"id": "Claude Opus 4.7"},
                "transcript_path": self.t_recent,
                "terminal_width": 100,
                "quota": self.quota,
                "context_window": {
                    "context_window_size": 200000,
                    "total_input_tokens": 150000,
                    "used_percentage": 75.0,
                    "current_usage": {
                        "input_tokens": 10000,
                        "cache_read_input_tokens": 140000,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 800,
                    },
                },
            }
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("22:44过期", lines[0])
        self.assertNotIn("约22:44", lines[0])
        self.assertEqual(lines[1], "限额(3P) 5h剩40% (3时29分后重置) │ 周剩12% (6天23时后重置)")

    def test_hydrogen_and_expired_labels(self) -> None:
        lines_h = self._plain_lines(
            {
                "model": {"id": "Gemini 4 Hydrogen (High)"},
                "transcript_path": self.t_recent,
                "terminal_width": 100,
                "context_window": {
                    "context_window_size": 1048576,
                    "total_input_tokens": 50000,
                    "used_percentage": 4.8,
                    "current_usage": {"input_tokens": 2000, "cache_read_input_tokens": 48000},
                },
            }
        )
        self.assertEqual(len(lines_h), 1)  # no quota -> single line
        self.assertIn("约22:44易冷", lines_h[0])

        lines_old = self._plain_lines(
            {
                "model": {"id": "Gemini 4 Neon (XHigh)"},
                "transcript_path": self.t_old,
                "terminal_width": 100,
                "context_window": {
                    "context_window_size": 1048576,
                    "total_input_tokens": 50000,
                    "used_percentage": 4.8,
                    "current_usage": {"input_tokens": 2000, "cache_read_input_tokens": 48000},
                },
            }
        )
        self.assertIn("约已过期", lines_old[0])

    def test_hydration_window_zero_no_fake_zero_percent(self) -> None:
        lines = self._plain_lines(
            {
                "model": {"id": "Gemini 4 Neon"},
                "transcript_path": self.t_recent,
                "context_window": {
                    "context_window_size": 0,
                    "total_input_tokens": 128000,
                    "used_percentage": 0,
                    "current_usage": {"input_tokens": 4000, "cache_read_input_tokens": 120000},
                },
            }
        )
        self.assertNotIn("0.0%", lines[0])
        self.assertIn("上下文 128k", lines[0])

    def test_width_adaptation_no_overflow(self) -> None:
        payload = {
            "model": {"id": "Gemini 4 Neon (XHigh)"},
            "transcript_path": self.t_recent,
            "quota": self.quota,
            "context_window": {
                "context_window_size": 1048576,
                "total_input_tokens": 185000,
                "used_percentage": 17.64,
                "current_usage": {"input_tokens": 8000, "cache_read_input_tokens": 168000},
            },
        }
        for cols in (110, 80, 65, 52, 46):
            p = dict(payload, terminal_width=cols)
            for ln in sl.render(p, now_local=NOW).splitlines():
                self.assertLessEqual(sl.visible_width(ln), cols - 2)


    def test_compact_checkpoint_resets_cache_and_updates_context(self) -> None:
        t_compact = make_compaction_transcript("2026-11-23T14:40:00Z", summary_tokens=4287)
        try:
            payload = {
                "model": {"id": "gemini-4-carbon-high"},
                "transcript_path": t_compact,
                "terminal_width": 100,
                "quota": self.quota,
                "context_window": {
                    "context_window_size": 1048576,
                    "total_input_tokens": 451000,
                    "used_percentage": 43.0,
                },
            }
            lines = self._plain_lines(payload)
            self.assertEqual(len(lines), 2)
            self.assertIn("2.2% (23k/1M)", lines[0])
            self.assertNotIn("43.0%", lines[0])
            self.assertNotIn("451k", lines[0])
            self.assertIn("缓存 0% (已重置)", lines[0])
            self.assertNotIn("463k", lines[0])
            self.assertNotIn("约已过期", lines[0])
        finally:
            if os.path.exists(t_compact):
                os.remove(t_compact)

    def test_post_compaction_subsequent_turn_resumes_normal_metrics(self) -> None:
        t_next = make_compaction_transcript("2026-11-23T14:40:00Z", summary_tokens=4287, next_turn_ts="2026-11-23T14:41:00Z")
        try:
            payload = {
                "model": {"id": "gemini-4-carbon-high"},
                "transcript_path": t_next,
                "terminal_width": 100,
                "quota": self.quota,
                "context_window": {
                    "context_window_size": 1048576,
                    "total_input_tokens": 18000,
                    "used_percentage": 1.7,
                },
            }
            lines = self._plain_lines(payload)
            self.assertEqual(len(lines), 2)
            self.assertIn("1.7% (18k/1M)", lines[0])
            self.assertIn("缓存 0% (未命中)", lines[0])
        finally:
            if os.path.exists(t_next):
                os.remove(t_next)

    def test_reset_countdown_boundaries(self) -> None:
        # Past or missing -> empty
        self.assertEqual(sl.format_reset_time(None, NOW, False, False), "")
        self.assertEqual(sl.format_reset_time(NOW, NOW, False, False), "")
        self.assertEqual(sl.format_reset_time(NOW - timedelta(seconds=1), NOW, False, False), "")
        # Sub-minute rounds up to 1分
        self.assertEqual(sl.format_reset_time(NOW + timedelta(seconds=10), NOW, False, False), "1分后重置")
        self.assertEqual(sl.format_reset_time(NOW + timedelta(seconds=10), NOW, False, True), "1分")
        # Minutes only
        self.assertEqual(sl.format_reset_time(NOW + timedelta(minutes=42), NOW, False, False), "42分后重置")
        # Exact hours & hours+minutes with zero-padded minutes
        self.assertEqual(sl.format_reset_time(NOW + timedelta(hours=2), NOW, False, False), "2时后重置")
        self.assertEqual(sl.format_reset_time(NOW + timedelta(hours=1, minutes=5), NOW, False, False), "1时05分后重置")
        self.assertEqual(sl.format_reset_time(NOW + timedelta(hours=1, minutes=5), NOW, False, True), "1时05分")
        # 23h59m30s rounds up cleanly to 1天 instead of 24时
        self.assertEqual(sl.format_reset_time(NOW + timedelta(hours=23, minutes=59, seconds=30), NOW, True, False), "1天后重置")
        # Multi-day + hours & exact days
        self.assertEqual(sl.format_reset_time(NOW + timedelta(days=4, hours=16, minutes=14), NOW, True, False), "4天16时后重置")
        self.assertEqual(sl.format_reset_time(NOW + timedelta(days=4, hours=16, minutes=14), NOW, True, True), "4天16时")
        self.assertEqual(sl.format_reset_time(NOW + timedelta(days=6), NOW, True, False), "6天后重置")
        # Weekly window < 24h falls back to hours/minutes countdown
        self.assertEqual(sl.format_reset_time(NOW + timedelta(hours=18, minutes=7), NOW, True, False), "18时07分后重置")

    def test_claude_missing_3p_pool_never_falls_back_to_gemini_wide_and_narrow(self) -> None:
        gemini_only_quota = {
            "gemini-5h": {"remaining_fraction": 0.88, "reset_time": "2026-11-23T16:24:30Z"},
            "gemini-weekly": {"remaining_fraction": 0.85, "reset_time": "2026-11-29T07:36:48Z"},
        }
        for model_id in ("Claude Opus 4.7", "claude-sonnet-4-5", "gpt-4.1", "o3-mini"):
            self.assertEqual(sl.select_quota_buckets(gemini_only_quota, model_id), ("", None, None))
            for cols in (110, 80, 76, 65, 52, 46):
                lines = self._plain_lines(
                    {
                        "model": {"id": model_id},
                        "transcript_path": self.t_recent,
                        "terminal_width": cols,
                        "quota": gemini_only_quota,
                        "context_window": {
                            "context_window_size": 200000,
                            "total_input_tokens": 150000,
                            "used_percentage": 75.0,
                            "current_usage": {"input_tokens": 10000, "cache_read_input_tokens": 140000},
                        },
                    }
                )
                self.assertEqual(len(lines), 1, f"Expected single line for {model_id} at {cols} cols, got: {lines}")
                self.assertNotIn("限额", lines[0])
                self.assertNotIn("88%", lines[0])
                self.assertNotIn("85%", lines[0])

    def test_gemini_missing_gemini_pool_never_falls_back_to_3p_wide_and_narrow(self) -> None:
        three_p_only_quota = {
            "3p-5h": {"remaining_fraction": 0.40, "reset_time": "2026-11-23T18:10:00Z"},
            "3p-weekly": {"remaining_fraction": 0.12, "reset_time": "2026-11-30T14:00:00Z"},
        }
        for model_id in ("Gemini 4 Neon (XHigh)", "gemini-4-carbon-high", "Gemini 4 Hydrogen (High)"):
            self.assertEqual(sl.select_quota_buckets(three_p_only_quota, model_id), ("", None, None))
            for cols in (110, 80, 76, 65, 52, 46):
                lines = self._plain_lines(
                    {
                        "model": {"id": model_id},
                        "transcript_path": self.t_recent,
                        "terminal_width": cols,
                        "quota": three_p_only_quota,
                        "context_window": {
                            "context_window_size": 1048576,
                            "total_input_tokens": 185000,
                            "used_percentage": 17.64,
                            "current_usage": {"input_tokens": 8000, "cache_read_input_tokens": 168000},
                        },
                    }
                )
                self.assertEqual(len(lines), 1, f"Expected single line for {model_id} at {cols} cols, got: {lines}")
                self.assertNotIn("限额", lines[0])
                self.assertNotIn("40%", lines[0])
                self.assertNotIn("12%", lines[0])

    def test_pool_classification_boundaries_and_partial_buckets(self) -> None:
        # Substring lookalikes like nano1 / pro3 / pro4 must NOT trigger OpenAI o1/o3/o4 3P classification
        for gm in ("gemini-nano1", "gemini-pro3", "gemini-pro4", "neon-xhigh"):
            self.assertEqual(sl.classify_model_pool(gm), "Gemini")
        for tp in ("Claude Haiku 4.5", "gpt-5", "o1", "o3-mini", "o4-mini", "openai/o3"):
            self.assertEqual(sl.classify_model_pool(tp), "3P")
        for unk in ("", "   ", "unknown-model", "mystery-model", "proto3", "claude-vs-gemini"):
            self.assertIsNone(sl.classify_model_pool(unk))
            self.assertEqual(sl.select_quota_buckets(self.quota, unk), ("", None, None))

        # Explicit unknown model in payload must NOT leak stale Gemini model from transcript_path
        lines_unk = self._plain_lines(
            {
                "model": {"id": "unknown-model"},
                "transcript_path": self.t_recent,
                "terminal_width": 60,
                "quota": self.quota,
                "context_window": {"context_window_size": 200000, "total_input_tokens": 50000, "used_percentage": 25.0},
            }
        )
        self.assertEqual(len(lines_unk), 1)

        # Opaque model.id with recognizable model.display_name resolves active pool properly
        lines_disp = self._plain_lines(
            {
                "model": {"id": "custom-endpoint-1", "display_name": "Claude Opus 4.7"},
                "transcript_path": self.t_recent,
                "terminal_width": 58,
                "quota": self.quota,
                "context_window": {"context_window_size": 200000, "total_input_tokens": 50000, "used_percentage": 25.0},
            }
        )
        self.assertEqual(len(lines_disp), 2)
        self.assertIn("限额(3P) 5h剩40%", lines_disp[1])

        # Partial 3P bucket (only 3p-5h, no 3p-weekly) never borrows gemini-weekly
        partial_quota = {
            "3p-5h": {"remaining_fraction": 0.40, "reset_time": "2026-11-23T18:10:00Z"},
            "gemini-weekly": {"remaining_fraction": 0.85, "reset_time": "2026-11-29T07:36:48Z"},
        }
        lbl, b5, bw = sl.select_quota_buckets(partial_quota, "Claude Opus 4.7")
        self.assertEqual(lbl, "3P")
        self.assertIsNotNone(b5)
        self.assertIsNone(bw)

        # Unscoped generic keys work when no pool prefixes exist, while foreign scoped keys are rejected
        unscoped = {"5h": {"remaining_fraction": 0.66, "reset_time": "2026-11-23T16:24:30Z"}}
        self.assertEqual(sl.select_quota_buckets(unscoped, "Claude Opus 4.7"), ("", unscoped["5h"], None))
        foreign = {"other-vendor-5h": {"remaining_fraction": 0.66}}
        self.assertEqual(sl.select_quota_buckets(foreign, "Claude Opus 4.7"), ("", None, None))

    def test_malformed_fields_graceful_degradation(self) -> None:
        lines = self._plain_lines(
            {
                "model": {"id": "Claude Opus 4.7"},
                "transcript_path": self.t_recent,
                "terminal_width": "auto",
                "quota": {
                    "3p-5h": {"remaining_fraction": True},
                    "3p-weekly": {"remaining_fraction": 0.25, "reset_time": "2026-11-30T14:00:00Z"},
                },
                "context_window": {
                    "context_window_size": "200000",
                    "total_input_tokens": 50000,
                    "used_percentage": float("nan"),
                },
            }
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("25.0% (50k/200k)", lines[0])
        self.assertEqual(lines[1], "限额(3P) 周剩25% (6天23时后重置)")

if __name__ == "__main__":
    unittest.main()
