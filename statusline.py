#!/usr/bin/env python3
"""Antigravity CLI (agy) custom status line (2-line layout):
  Line 1: Context progress bar + Prompt cache hit rate & estimated expiry
  Line 2: 5h & Weekly remaining quota + reset countdown (auto-matches Gemini vs 3P pool)

Designed for stack_with_default: true in ~/.gemini/antigravity-cli/settings.json.
Pure Python standard library, zero external dependencies, CJK-aware width adaptation.
"""

from __future__ import annotations

import json
import math
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
_GEMINI_SUBSTR_RE = re.compile(r"gemini")
_GEMINI_CODENAME_RE = re.compile(r"(?<![a-z0-9])(?:neon|hydrogen|carbon)(?![a-z0-9])")
_3P_SUBSTR_RE = re.compile(r"(?:claude|anthropic|opus|sonnet|haiku|gpt|openai)")
_3P_O_SERIES_RE = re.compile(r"(?<![a-z0-9])o[134](?:$|[^a-z0-9]|mini|pro|preview)")
_UNSCOPED_5H_KEYS = frozenset({"5h", "five_hour", "five-hour", "session"})
_UNSCOPED_WEEKLY_KEYS = frozenset({"weekly", "week", "7d", "seven_day", "seven-day"})


def _safe_int(val: Any, default: int = 0) -> int:
    if isinstance(val, bool) or val is None:
        return default
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val) if math.isfinite(val) else default
    if isinstance(val, str):
        s = val.strip()
        if s:
            try:
                f = float(s)
                return int(f) if math.isfinite(f) else default
            except ValueError:
                return default
    return default


def classify_model_pool(model_name: str | None) -> str | None:
    """Identify the quota pool ('Gemini' or '3P') for model_name, or None if unrecognized."""
    ml = (model_name or "").strip().lower()
    if not ml:
        return None
    is_3p = bool(_3P_SUBSTR_RE.search(ml) or _3P_O_SERIES_RE.search(ml))
    is_gemini = bool(_GEMINI_SUBSTR_RE.search(ml) or (not is_3p and _GEMINI_CODENAME_RE.search(ml)))
    if is_gemini and is_3p:
        return None
    if is_gemini:
        return "Gemini"
    if is_3p:
        return "3P"
    return None


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
    if n is None or isinstance(n, bool):
        return "0"
    try:
        f = float(n)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(f):
        return "0"
    val = max(0.0, f)
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


def parse_iso_local(iso_ts: str | None, tz: Any = None) -> datetime | None:
    if not iso_ts or not isinstance(iso_ts, str):
        return None
    try:
        ts = iso_ts.strip()
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        return dt.astimezone(tz) if tz is not None else dt.astimezone()
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
    last_dt = parse_iso_local(last_ts_iso, now_local.tzinfo)
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
    window_size = max(0, _safe_int(cw.get("context_window_size")))
    raw_pct = cw.get("used_percentage")
    cur = cw.get("current_usage") if isinstance(cw.get("current_usage"), dict) else None

    # Handle post-compaction state where latest turn event is CHECKPOINT
    if last_rec and last_rec.get("type") == "CHECKPOINT":
        summary_tokens = max(0, _safe_int(last_rec.get("output_tokens")))
        base_overhead = 18_700  # System instructions, skills, tools baseline overhead
        used_tokens = base_overhead + summary_tokens
        used_pct = (used_tokens / window_size * 100.0) if window_size > 0 else 0.0
        used_pct = max(0.0, min(100.0, used_pct))
        return used_pct, used_tokens, window_size

    cur_tokens = 0
    if cur:
        cur_tokens = (
            max(0, _safe_int(cur.get("input_tokens")))
            + max(0, _safe_int(cur.get("cache_read_input_tokens")))
            + max(0, _safe_int(cur.get("cache_creation_input_tokens")))
            + max(0, _safe_int(cur.get("output_tokens")))
        )
    elif last_rec:
        cur_tokens = (
            max(0, _safe_int(last_rec.get("input_tokens")))
            + max(0, _safe_int(last_rec.get("cache_read_tokens")))
            + max(0, _safe_int(last_rec.get("output_tokens")))
        )

    total_in_reported = max(0, _safe_int(cw.get("total_input_tokens")))
    if isinstance(raw_pct, (int, float)) and not isinstance(raw_pct, bool) and math.isfinite(raw_pct):
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
        inp = max(0, _safe_int(cur.get("input_tokens")))
        cr = max(0, _safe_int(cur.get("cache_read_input_tokens")))
        cc = max(0, _safe_int(cur.get("cache_creation_input_tokens")))
        if inp > 0 or cr > 0 or cc > 0 or _safe_int(cur.get("output_tokens")) > 0:
            has_usage = True

    if not has_usage and last_rec is not None:
        inp = max(0, _safe_int(last_rec.get("input_tokens")))
        cr = max(0, _safe_int(last_rec.get("cache_read_tokens")))
        cc = 0
        if inp > 0 or cr > 0 or _safe_int(last_rec.get("output_tokens")) > 0:
            has_usage = True

    total_in = inp + cr + cc
    if not has_usage or total_in <= 0:
        return False, 0.0, 0

    return True, (cr / total_in) * 100.0, cr


def select_quota_buckets(
    quota: dict[str, Any],
    model_name: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Pick (pool_label, five_hour_bucket, weekly_bucket) strictly matching the active model pool.

    Never falls back across model pools (e.g. never returns Gemini quota for Claude/GPT or vice versa),
    preventing silent cross-pool misattribution especially on narrow (<78 col) terminals where pool tags hide.
    """
    if not isinstance(quota, dict) or not quota:
        return "", None, None

    norm: dict[str, Any] = {str(k).strip().lower(): v for k, v in quota.items()}
    has_named_pools = any(
        k.startswith(("gemini-", "gemini_", "1p-", "1p_", "3p-", "3p_"))
        for k in norm
    )

    pool = classify_model_pool(model_name)
    if has_named_pools:
        if pool == "Gemini":
            prefixes, label = ("gemini-", "gemini_", "1p-", "1p_"), "Gemini"
        elif pool == "3P":
            prefixes, label = ("3p-", "3p_"), "3P"
        else:
            return "", None, None

        five_h = next((norm[f"{p}5h"] for p in prefixes if isinstance(norm.get(f"{p}5h"), dict)), None)
        weekly = next((norm[f"{p}weekly"] for p in prefixes if isinstance(norm.get(f"{p}weekly"), dict)), None)
        if five_h is not None or weekly is not None:
            return label, five_h, weekly
        return "", None, None

    # Unscoped generic fallback only when quota uses pool-agnostic keys (never foreign model-scoped keys)
    five_h = next((val for k, val in norm.items() if k in _UNSCOPED_5H_KEYS and isinstance(val, dict)), None)
    weekly = next((val for k, val in norm.items() if k in _UNSCOPED_WEEKLY_KEYS and isinstance(val, dict)), None)
    return "", five_h, weekly


def format_reset_time(reset_dt: datetime | None, now_local: datetime, is_weekly: bool, short: bool) -> str:
    """Format remaining time until quota reset as an intuitive countdown (e.g. 5天16时后重置 / 1时44分后重置)."""
    del is_weekly  # Duration-based formatting naturally handles both 5h and weekly (including <24h) windows.
    if not reset_dt or reset_dt <= now_local:
        return ""
    delta_sec = (reset_dt - now_local).total_seconds()
    total_mins = max(1, int(math.ceil(delta_sec / 60.0)))

    days = total_mins // (24 * 60)
    rem_mins = total_mins % (24 * 60)
    hours = rem_mins // 60
    mins = rem_mins % 60

    if days > 0:
        dur = f"{days}天{hours}时" if hours > 0 else f"{days}天"
    elif hours > 0:
        dur = f"{hours}时{mins:02d}分" if mins > 0 else f"{hours}时"
    else:
        dur = f"{mins}分"

    return dur if short else f"{dur}后重置"


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
    """Line 2: 5h & Weekly remaining quota with reset countdown."""
    pool_label, five_h, weekly = select_quota_buckets(quota, model_name)
    if five_h is None and weekly is None:
        return ""

    def parse_bucket(bucket: dict[str, Any] | None) -> tuple[float | None, datetime | None, bool]:
        if not isinstance(bucket, dict):
            return None, None, False
        frac = bucket.get("remaining_fraction")
        if not isinstance(frac, (int, float)) or isinstance(frac, bool) or not math.isfinite(frac):
            return None, None, bool(bucket.get("disabled"))
        rem = max(0.0, min(100.0, float(frac) * 100.0))
        return rem, parse_iso_local(bucket.get("reset_time"), now_local.tzinfo), bool(bucket.get("disabled"))

    rem5, reset5_dt, dis5 = parse_bucket(five_h)
    remw, resetw_dt, disw = parse_bucket(weekly)
    if rem5 is None and remw is None:
        return ""

    tiers = [
        dict(show_prefix=True, show_pool=True, show_5h_reset=True, show_w_reset=True, short_time=False, compact=False),
        dict(show_prefix=True, show_pool=True, show_5h_reset=True, show_w_reset=True, short_time=True, compact=False),
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
    if now_local is None:
        now = datetime.now().astimezone()
    elif now_local.tzinfo is None:
        now = now_local.astimezone()
    else:
        now = now_local
    cw = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    quota = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
    model_obj = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    transcript_path = payload.get("transcript_path") if isinstance(payload.get("transcript_path"), str) else ""
    raw_cid = payload.get("conversation_id") or payload.get("session_id") or ""
    conversation_id = raw_cid if isinstance(raw_cid, str) else ""
    cols = _safe_int(payload.get("terminal_width"), default=100)
    if cols <= 0:
        cols = 100
    max_width = max(24, cols - 2)

    last_rec = read_last_planner_response(transcript_path, conversation_id)
    model_id = str(model_obj.get("id") or "").strip()
    model_disp = str(model_obj.get("display_name") or "").strip()
    rec_model = str((last_rec or {}).get("model") or "").strip()
    if model_id or model_disp:
        if classify_model_pool(model_id) is None and classify_model_pool(model_disp) is not None:
            model_name = model_disp
        else:
            model_name = model_id or model_disp
    else:
        model_name = rec_model
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
