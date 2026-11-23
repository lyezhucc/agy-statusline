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
        self.assertEqual(lines[1], "限额(Gemini) 5h剩88% (00:24重置) │ 周剩85% (11-29 15:36重置)")

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
        self.assertEqual(lines[1], "限额(3P) 5h剩40% (02:10重置) │ 周剩12% (11-30 22:00重置)")

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


if __name__ == "__main__":
    unittest.main()
