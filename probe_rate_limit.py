#!/usr/bin/env python3
"""Probe OGS rate limits for /api/v1/reviews/<id>/sgf.

Strategy:
  1. Fire N requests with a configurable delay; record status, latency, any
     Retry-After / X-RateLimit-* headers, and the number of consecutive 200s
     before the first 429.
  2. After a 429, sleep and probe how long until the limit resets.

We use real review IDs from reviews.json so we don't hammer a single resource.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REVIEWS_FILE = Path(__file__).parent / "reviews.json"
USER_AGENT = "egf-review-archiver/0.1 (rate-limit probe)"


def head_or_get(url: str) -> tuple[int, dict[str, str], int, float]:
    """Return (status, headers, body_size, elapsed_seconds)."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    t0 = time.monotonic()
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, dict(resp.headers), len(body), time.monotonic() - t0
    except HTTPError as e:
        body = e.read() if e.fp else b""
        return e.code, dict(e.headers or {}), len(body), time.monotonic() - t0


def run_burst(ids: list[int], delay_s: float, label: str) -> dict[str, int]:
    print(f"\n=== {label}: {len(ids)} requests, {delay_s}s delay ===")
    stats = {"200": 0, "429": 0, "other": 0, "consecutive_200_before_first_429": 0}
    first_429_at = None
    interesting_headers = (
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "ratelimit-limit",
        "ratelimit-remaining",
        "ratelimit-reset",
    )
    for i, rid in enumerate(ids):
        url = f"https://online-go.com/api/v1/reviews/{rid}/sgf"
        status, headers, size, elapsed = head_or_get(url)
        rl = {k: v for k, v in headers.items() if k.lower() in interesting_headers}
        marker = ""
        if status == 200:
            stats["200"] += 1
            if first_429_at is None:
                stats["consecutive_200_before_first_429"] += 1
        elif status == 429:
            stats["429"] += 1
            if first_429_at is None:
                first_429_at = i
                marker = "  <-- FIRST 429"
        else:
            stats["other"] += 1
        rl_str = f"  rl={rl}" if rl else ""
        print(f"  [{i:>3}] id={rid} status={status} size={size}B {elapsed * 1000:.0f}ms{rl_str}{marker}")
        if i < len(ids) - 1 and delay_s > 0:
            time.sleep(delay_s)
    print(f"  summary: {stats}")
    return stats


def main() -> int:
    d = json.loads(REVIEWS_FILE.read_text())
    all_ids = [r["id"] for r in d["reviews"]]
    print(f"Loaded {len(all_ids)} review ids")

    run_burst(all_ids[:30], delay_s=0.0, label="A) Burst, 0s delay")
    print("\n--- waiting 30s for any backoff window to clear ---")
    time.sleep(30)
    run_burst(all_ids[30:60], delay_s=0.5, label="B) Steady, 0.5s delay")
    print("\n--- waiting 30s ---")
    time.sleep(30)
    run_burst(all_ids[60:90], delay_s=1.0, label="C) Steady, 1.0s delay")
    print("\n--- waiting 30s ---")
    time.sleep(30)
    run_burst(all_ids[90:120], delay_s=2.0, label="D) Steady, 2.0s delay")

    return 0


if __name__ == "__main__":
    sys.exit(main())
