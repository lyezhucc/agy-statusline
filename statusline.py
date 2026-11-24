#!/usr/bin/env python3
"""Antigravity CLI (agy) custom status line (2-line layout):
  Line 1: Context progress bar + Prompt cache hit rate & estimated expiry
  Line 2: 5h & Weekly remaining quota + reset timestamps (auto-matches Gemini vs 3P pool)

Designed for stack_with_default: true in ~/.gemini/antigravity-cli/settings.json.
Pure Python standard library, zero external dependencies, CJK-aware width adaptation.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from typing import Any

# Standard ANSI 16-color codes (safe across dark/light terminal themes)
RESET = "\033[0m"
BOLD = "\033[1m"
FG_RED = "\033[91m"
FG_GREEN = "\033[32m"
FG_YELLOW = "\033[33m"
FG_CYAN = "\033[36m"
FG_GRAY = "\033[90m"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def visible_width(text: str) -> int:
    """Compute display column width in terminal cells (CJK wide chars = 2)."""
    plain = ANSI_RE.sub("", text)
    width = 0
    for ch in plain:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def format_tokens(n: int | float | None) -> str:
    """Format token counts compactly (e.g. 850, 7.4k, 135k, 1M)."""
    if n is None:
        return "0"
    val = max(0.0, float(n))
    if val >= 1_000_000:
        m = val / 1_000_000.0
        if abs(val - 1_048_576) <= 1024 or abs(m - round(m)) < 0.05:
            return f"{int(round(m))}M"
        return f"{m:.1f}M"
    if val >= 10_000:
        return f"{int(round(val / 1000.0))}k"
    if val >= 1_000:
        return f"{val / 1000.0:.1f}k"
    return str(int(round(val)))


def build_bar(pct: float, width: int, color: str) -> str:
    clamped = max(0.0, min(100.0, pct))
    filled = max(0, min(width, int(round(clamped / 100.0 * width))))
    return f"{color}{'█' * filled}{FG_GRAY}{'░' * (width - filled)}{RESET}"


def context_color(pct: float) -> str:
    if pct >= 85.0:
        return FG_RED
    if pct >= 70.0:
        return FG_YELLOW
    return FG_GREEN


def cache_color(hit_pct: float, is_miss: bool) -> str:
    if is_miss:
        return FG_YELLOW
    if hit_pct >= 70.0:
        return FG_GREEN
    if hit_pct >= 30.0:
        return FG_YELLOW
    return FG_RED


def quota_color(rem_pct: float, disabled: bool = False) -> str:
    if disabled or rem_pct <= 15.0:
        return FG_RED
    if rem_pct <= 40.0:
        return FG_YELLOW
    return FG_GREEN


def parse_iso_local(iso_ts: str | None) -> datetime | None:
    if not iso_ts or not isinstance(iso_ts, str):
        return None
    try:
        ts = iso_ts.strip()
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).astimezone()
    except Exception:
        return None


def resolve_transcript_path(transcript_path: str, conversation_id: str) -> str | None:
    """Resolve real transcript.jsonl path (works around agy passing antigravity/ instead of antigravity-cli/)."""
    candidates: list[str] = []
    if transcript_path:
        expanded = os.path.expanduser(transcript_path)
        candidates.append(expanded)
        if "/.gemini/antigravity/" in expanded:
            candidates.append(expanded.replace("/.gemini/antigravity/", "/.gemini/antigravity-cli/", 1))
    if conversation_id:
        home = os.path.expanduser("~")
        for sub in ("antigravity-cli", "antigravity"):
            candidates.append(
                os.path.join(home, ".gemini", sub, "brain", conversation_id, ".system_generated", "logs", "transcript.jsonl")
            )

    seen: set[str] = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        if os.path.isfile(p):
            return p
    return None


def read_last_planner_response(transcript_path: str, conversation_id: str = "") -> dict[str, Any] | None:
    """Efficiently read the latest PLANNER_RESPONSE or CHECKPOINT entry from the tail of transcript.jsonl."""
    path = resolve_transcript_path(transcript_path, conversation_id)
    if not path:
        return None

    try:
        size = os.path.getsize(path)
        if size == 0:
            return None

        for chunk_bytes in (65_536, 524_288, 4_194_304, size):
            read_len = min(size, chunk_bytes)
            with open(path, "rb") as fh:
                fh.seek(size - read_len)
                raw = fh.read(read_len)

            lines = raw.decode("utf-8", errors="replace").splitlines()
            if read_len < size and lines:
                lines = lines[1:]

            for line in reversed(lines):
                if '"PLANNER_RESPONSE"' not in line and '"CHECKPOINT"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                rec_type = rec.get("type")
                if rec_type in ("PLANNER_RESPONSE", "CHECKPOINT"):
                    return rec

            if read_len >= size:
                break
    except Exception:
        return None

    return None


def infer_cache_window(model_name: str) -> tuple[int, bool, str]:
    """Return (ttl_minutes, is_approximate, suffix_label) by model family.

    - Claude: explicit 5m TTL -> exact expiry timestamp (`HH:MM过期`).
    - Gemini 4 Neon: implicit LRU cache empirically warm ~60m (`约HH:MM过期`).
    - Gemini 4 Hydrogen: implicit LRU cache empirically cools faster ~5m (`约HH:MM易冷`).
    - Others: conservative 5m approximate reference (`约HH:MM过期`).
    """
    ml = (model_name or "").lower()
    if "claude" in ml:
        return 5, False, "过期"
    if "neon" in ml:
        return 60, True, "过期"
    if "hydrogen" in ml:
        return 5, True, "易冷"
    return 5, True, "过期"


def format_cache_expiry(
    last_ts_iso: str | None,
    model_name: str,
    now_local: datetime,
    compact_label: bool = False,
) -> tuple[str, str]:
    """Return (display_text, ansi_color) for cache expiry indicator."""
    last_dt = parse_iso_local(last_ts_iso)
    if not last_dt:
        return "", FG_GRAY

    ttl_min, is_approx, suffix = infer_cache_window(model_name)
    exp_dt = last_dt + timedelta(minutes=ttl_min)
    rem_sec = (exp_dt - now_local).total_seconds()
    prefix = "约" if is_approx else ""

    if rem_sec <= 0:
        return (f"{prefix}已过期" if not compact_label else "已过期"), FG_GRAY

    hhmm = exp_dt.strftime("%H:%M")
    col = FG_YELLOW if rem_sec <= 60 else FG_CYAN
    if compact_label:
        return f"{prefix}{hhmm}", col
    return f"{prefix}{hhmm}{suffix}", col


def compute_context_metrics(
    cw: dict[str, Any],
    last_rec: dict[str, Any] | None,
) -> tuple[float, int, int]:
    """Return (used_pct, used_tokens, window_size)."""
    window_size = int(cw.get("context_window_size") or 0)
    raw_pct = cw.get("used_percentage")
    cur = cw.get("current_usage") if isinstance(cw.get("current_usage"), dict) else None

    # Handle post-compaction state where latest turn event is CHECKPOINT
    if last_rec and last_rec.get("type") == "CHECKPOINT":
        summary_tokens = int(last_rec.get("output_tokens") or 0)
        base_overhead = 18_700  # System instructions, skills, tools baseline overhead
        used_tokens = base_overhead + summary_tokens
        used_pct = (used_tokens / window_size * 100.0) if window_size > 0 else 0.0
        used_pct = max(0.0, min(100.0, used_pct))
        return used_pct, used_tokens, window_size

    cur_tokens = 0
    if cur:
        cur_tokens = (
            int(cur.get("input_tokens") or 0)
            + int(cur.get("cache_read_input_tokens") or 0)
            + int(cur.get("cache_creation_input_tokens") or 0)
            + int(cur.get("output_tokens") or 0)
        )
    elif last_rec:
        cur_tokens = (
            int(last_rec.get("input_tokens") or 0)
            + int(last_rec.get("cache_read_tokens") or 0)
            + int(last_rec.get("output_tokens") or 0)
        )

    total_in_reported = int(cw.get("total_input_tokens") or 0)
    if isinstance(raw_pct, (int, float)):
        used_pct = max(0.0, min(100.0, float(raw_pct)))
        if window_size > 0:
            pct_tokens = int(round(used_pct / 100.0 * window_size))
            if total_in_reported > 0 and abs(total_in_reported - pct_tokens) <= max(64, int(window_size * 0.001)):
                used_tokens = total_in_reported
            else:
                used_tokens = pct_tokens if pct_tokens > 0 else cur_tokens
        else:
            used_tokens = total_in_reported if total_in_reported > 0 else cur_tokens
    else:
        used_tokens = total_in_reported if total_in_reported > 0 else cur_tokens
        used_pct = (used_tokens / window_size * 100.0) if window_size > 0 else 0.0
        used_pct = max(0.0, min(100.0, used_pct))

    return used_pct, used_tokens, window_size


def compute_cache_metrics(
    cw: dict[str, Any],
    last_rec: dict[str, Any] | None,
) -> tuple[bool, float, int]:
    """Return (has_data, hit_pct, cache_read_tokens)."""
    # In post-compaction state (CHECKPOINT), previous prompt cache prefix is invalidated
    if last_rec and last_rec.get("type") == "CHECKPOINT":
        return True, 0.0, 0

    cur = cw.get("current_usage") if isinstance(cw.get("current_usage"), dict) else None

    inp = cr = cc = 0
    has_usage = False

    if cur is not None:
        inp = int(cur.get("input_tokens") or 0)
        cr = int(cur.get("cache_read_input_tokens") or 0)
        cc = int(cur.get("cache_creation_input_tokens") or 0)
        if inp > 0 or cr > 0 or cc > 0 or int(cur.get("output_tokens") or 0) > 0:
            has_usage = True

    if not has_usage and last_rec is not None:
        inp = int(last_rec.get("input_tokens") or 0)
        cr = int(last_rec.get("cache_read_tokens") or 0)
        cc = 0
        if inp > 0 or cr > 0 or int(last_rec.get("output_tokens") or 0) > 0:
            has_usage = True

    total_in = inp + cr + cc
    if not has_usage or total_in <= 0:
        return False, 0.0, 0

    return True, (cr / total_in) * 100.0, cr


def select_quota_buckets(
    quota: dict[str, Any],
    model_name: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Pick (pool_label, five_hour_bucket, weekly_bucket) matching the active model pool."""
    if not quota:
        return "", None, None

    ml = (model_name or "").lower()
    prefer_3p = any(k in ml for k in ("claude", "gpt", "opus", "sonnet", "haiku", "o1", "o3", "o4"))
    order = (("3p-", "3P"), ("gemini-", "Gemini")) if prefer_3p else (("gemini-", "Gemini"), ("3p-", "3P"))

    for pref, label in order:
        five_h = quota.get(f"{pref}5h") if isinstance(quota.get(f"{pref}5h"), dict) else None
        weekly = quota.get(f"{pref}weekly") if isinstance(quota.get(f"{pref}weekly"), dict) else None
        if five_h is not None or weekly is not None:
            return label, five_h, weekly

    # Generic fallback for any future bucket naming
    five_h = weekly = None
    for key, val in quota.items():
        if not isinstance(val, dict):
            continue
        kl = key.lower()
        if five_h is None and ("5h" in kl or "five" in kl or "session" in kl):
            five_h = val
        elif weekly is None and ("week" in kl or "7d" in kl):
            weekly = val

    return "", five_h, weekly


def format_reset_time(reset_dt: datetime | None, now_local: datetime, is_weekly: bool, short: bool) -> str:
    """Format reset timestamp cleanly without stale countdown drift."""
    if not reset_dt or reset_dt <= now_local:
        return ""
    delta_hours = (reset_dt - now_local).total_seconds() / 3600.0
    if not is_weekly or delta_hours < 24.0:
        t = reset_dt.strftime("%H:%M")
    elif short:
        t = reset_dt.strftime("%m-%d")
    else:
        t = reset_dt.strftime("%m-%d %H:%M")
    return t if short else f"{t}重置"


def render_session_line(
    used_pct: float,
    used_tokens: int,
    window_size: int,
    has_cache: bool,
    hit_pct: float,
    cache_read: int,
    last_ts_iso: str | None,
    model_name: str,
    now_local: datetime,
    max_width: int,
    is_compacted: bool = False,
) -> str:
    """Line 1: Context window progress bar + Prompt cache hit & expiry."""
    tiers = [
        dict(bar_len=10, show_ctx_tokens=True, show_cache_tokens=True, compact_expiry=False, tight=False),
        dict(bar_len=6, show_ctx_tokens=True, show_cache_tokens=True, compact_expiry=False, tight=True),
        dict(bar_len=5, show_ctx_tokens=True, show_cache_tokens=False, compact_expiry=False, tight=True),
        dict(bar_len=0, show_ctx_tokens=True, show_cache_tokens=False, compact_expiry=False, tight=True),
        dict(bar_len=0, show_ctx_tokens=False, show_cache_tokens=False, compact_expiry=True, tight=True),
    ]

    last_line = ""
    for opt in tiers:
        sp = "" if opt["tight"] else " "
        if window_size <= 0:
            if used_tokens > 0:
                ctx_seg = f"{FG_GRAY}上下文{RESET} {FG_GREEN}{BOLD}{format_tokens(used_tokens)}{RESET}"
            else:
                ctx_seg = f"{FG_GRAY}上下文 待首轮{RESET}"
        else:
            c_col = context_color(used_pct)
            pct_str = f"{used_pct:.1f}%"
            tok_part = f"({format_tokens(used_tokens)}/{format_tokens(window_size)})" if opt["show_ctx_tokens"] else ""
            if opt["bar_len"] > 0:
                bar = build_bar(used_pct, opt["bar_len"], c_col)
                ctx_seg = f"{FG_GRAY}上下文{RESET} {bar} {c_col}{BOLD}{pct_str}{RESET}"
            else:
                ctx_seg = f"{FG_GRAY}上下文{RESET} {c_col}{BOLD}{pct_str}{RESET}"
            if tok_part:
                ctx_seg += f"{sp}{FG_GRAY}{tok_part}{RESET}"

        exp_text, exp_col = format_cache_expiry(last_ts_iso, model_name, now_local, compact_label=opt["compact_expiry"])
        if not has_cache:
            cache_seg = f"{FG_GRAY}缓存 待首轮{RESET}"
        elif cache_read <= 0:
            k_col = cache_color(0.0, is_miss=True)
            if is_compacted:
                miss_tag = "(冷)" if opt["tight"] else "(已重置)"
            else:
                miss_tag = "(冷)" if opt["tight"] else "(未命中)"
            cache_seg = f"{FG_GRAY}缓存{RESET} {k_col}{BOLD}0%{RESET}{sp}{FG_GRAY}{miss_tag}{RESET}"
            if exp_text and not is_compacted:
                cache_seg += f" {exp_col}{exp_text}{RESET}"
        else:
            k_col = cache_color(hit_pct, is_miss=False)
            hit_label = "<1%" if 0.0 < hit_pct < 1.0 else f"{int(round(hit_pct))}%"
            cache_seg = f"{FG_GRAY}缓存{RESET} {k_col}{BOLD}{hit_label}{RESET}"
            if opt["show_cache_tokens"]:
                cache_seg += f"{sp}{FG_GRAY}({format_tokens(cache_read)}){RESET}"
            if exp_text:
                cache_seg += f" {exp_col}{exp_text}{RESET}"

        line = f"{ctx_seg} {FG_GRAY}│{RESET} {cache_seg}"
        last_line = line
        if visible_width(line) <= max_width:
            return line
    return last_line


def render_quota_line(
    quota: dict[str, Any],
    model_name: str,
    now_local: datetime,
    max_width: int,
) -> str:
    """Line 2: 5h & Weekly remaining quota with reset timestamps."""
    pool_label, five_h, weekly = select_quota_buckets(quota, model_name)
    if five_h is None and weekly is None:
        return ""

    def parse_bucket(bucket: dict[str, Any] | None) -> tuple[float | None, datetime | None, bool]:
        if not isinstance(bucket, dict):
            return None, None, False
        frac = bucket.get("remaining_fraction")
        if not isinstance(frac, (int, float)):
            return None, None, bool(bucket.get("disabled"))
        rem = max(0.0, min(100.0, float(frac) * 100.0))
        return rem, parse_iso_local(bucket.get("reset_time")), bool(bucket.get("disabled"))

    rem5, reset5_dt, dis5 = parse_bucket(five_h)
    remw, resetw_dt, disw = parse_bucket(weekly)
    if rem5 is None and remw is None:
        return ""

    tiers = [
        dict(show_prefix=True, show_pool=True, show_5h_reset=True, show_w_reset=True, short_time=False, compact=False),
        dict(show_prefix=True, show_pool=False, show_5h_reset=True, show_w_reset=True, short_time=False, compact=False),
        dict(show_prefix=True, show_pool=False, show_5h_reset=True, show_w_reset=True, short_time=True, compact=False),
        dict(show_prefix=False, show_pool=False, show_5h_reset=True, show_w_reset=False, short_time=True, compact=False),
        dict(show_prefix=False, show_pool=False, show_5h_reset=False, show_w_reset=False, short_time=True, compact=True),
    ]

    last_line = ""
    for opt in tiers:
        segs: list[str] = []
        if rem5 is not None:
            col = quota_color(rem5, dis5)
            pct_i = int(round(rem5))
            lbl = "5h:" if opt["compact"] else "5h剩"
            s = f"{FG_GRAY}{lbl}{RESET}{col}{BOLD}{pct_i}%{RESET}"
            if opt["show_5h_reset"] and rem5 < 99.5:
                rt = format_reset_time(reset5_dt, now_local, is_weekly=False, short=opt["short_time"])
                if rt:
                    s += f" {FG_GRAY}({rt}){RESET}"
            segs.append(s)

        if remw is not None:
            col = quota_color(remw, disw)
            pct_i = int(round(remw))
            lbl = "周:" if opt["compact"] else "周剩"
            s = f"{FG_GRAY}{lbl}{RESET}{col}{BOLD}{pct_i}%{RESET}"
            if opt["show_w_reset"] and remw < 99.5:
                rt = format_reset_time(resetw_dt, now_local, is_weekly=True, short=opt["short_time"])
                if rt:
                    s += f" {FG_GRAY}({rt}){RESET}"
            segs.append(s)

        body = f" {FG_GRAY}│{RESET} ".join(segs)
        if opt["show_prefix"]:
            tag = f"限额({pool_label})" if (opt["show_pool"] and pool_label) else "限额"
            line = f"{FG_GRAY}{tag}{RESET} {body}"
        else:
            line = body

        last_line = line
        if visible_width(line) <= max_width:
            return line
    return last_line


def render(payload: dict[str, Any], now_local: datetime | None = None) -> str:
    now = now_local.astimezone() if now_local else datetime.now().astimezone()
    cw = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    quota = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
    model_obj = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    transcript_path = payload.get("transcript_path") or ""
    conversation_id = payload.get("conversation_id") or payload.get("session_id") or ""
    cols = int(payload.get("terminal_width") or 100)
    max_width = max(24, cols - 2)

    last_rec = read_last_planner_response(transcript_path, conversation_id)
    model_name = (
        str(model_obj.get("id") or model_obj.get("display_name") or "")
        or str((last_rec or {}).get("model") or "")
    )
    last_ts_iso = (last_rec or {}).get("created_at")

    is_compacted = bool(last_rec and last_rec.get("type") == "CHECKPOINT")
    used_pct, used_tokens, window_size = compute_context_metrics(cw, last_rec)
    has_cache, hit_pct, cache_read = compute_cache_metrics(cw, last_rec)

    line1 = render_session_line(
        used_pct,
        used_tokens,
        window_size,
        has_cache,
        hit_pct,
        cache_read,
        last_ts_iso,
        model_name,
        now,
        max_width,
        is_compacted=is_compacted,
    )
    line2 = render_quota_line(quota, model_name, now, max_width)
    return f"{line1}\n{line2}" if line2 else line1


def main() -> int:
    raw = sys.stdin.read()
    dump_path = os.environ.get("AGY_STATUSLINE_DUMP")
    if dump_path:
        try:
            with open(dump_path, "w", encoding="utf-8") as fh:
                fh.write(raw)
        except Exception:
            pass

    if not raw.strip():
        print(f"{FG_GRAY}上下文 -- │ 缓存 待首轮{RESET}")
        return 0

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        print(f"{FG_GRAY}上下文 -- │ 缓存 --{RESET}")
        return 0

    print(render(payload))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print(f"{FG_GRAY}上下文 -- │ 缓存 --{RESET}")
        raise SystemExit(0)
