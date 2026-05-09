#!/usr/bin/env python3
"""Download all OGS review SGFs listed in reviews.json into ./sgfs/<id>.sgf.

Rate limit (probed empirically): ~10 requests per ~60 second sliding window,
no informational headers. We use a token-bucket scheduler tracking the last
10 request timestamps and waiting until the oldest is >= WINDOW_S in the past
before issuing the 11th. On unexpected 429 we sleep progressively longer.

Resumable: any file already present on disk is skipped, so re-running is safe.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
REVIEWS_FILE = ROOT / "reviews.json"
SGF_DIR = ROOT / "sgfs"

USER_AGENT = "egf-review-archiver/0.1 (+local research script)"
BUCKET_SIZE = 10
WINDOW_S = 65.0
MAX_429_RETRIES = 6
MAX_CONSECUTIVE_429 = 30


def http_get_bytes(url: str) -> tuple[int, bytes]:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        body = e.read() if e.fp else b""
        return e.code, body


def schedule_wait(history: deque[float]) -> None:
    if len(history) < BUCKET_SIZE:
        return
    oldest = history[0]
    wait = WINDOW_S - (time.monotonic() - oldest)
    if wait > 0:
        print(f"    [bucket full, sleeping {wait:.1f}s]", flush=True)
        time.sleep(wait)


def download_one(review_id: int, out_path: Path, history: deque[float]) -> str:
    """Returns 'ok', 'skip', or 'fail'."""
    if out_path.exists() and out_path.stat().st_size > 0:
        return "skip"
    url = f"https://online-go.com/api/v1/reviews/{review_id}/sgf"
    for attempt in range(MAX_429_RETRIES):
        schedule_wait(history)
        history.append(time.monotonic())
        if len(history) > BUCKET_SIZE:
            history.popleft()
        try:
            status, body = http_get_bytes(url)
        except URLError as e:
            print(f"    network error on {review_id}: {e}; sleeping 30s", flush=True)
            time.sleep(30)
            continue
        if status == 200:
            out_path.write_bytes(body)
            return "ok"
        if status == 429:
            backoff = min(180, 30 * (2**attempt))
            print(
                f"    UNEXPECTED 429 on {review_id} (attempt {attempt + 1}); sleeping {backoff}s",
                flush=True,
            )
            time.sleep(backoff)
            continue
        print(f"    HTTP {status} on {review_id}, body={body[:120]!r}", flush=True)
        return "fail"
    return "fail"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--limit", type=int, default=0, help="Stop after N downloads (0 = no limit)."
    )
    p.add_argument(
        "--source",
        type=Path,
        default=REVIEWS_FILE,
        help="JSON file with {'reviews': [{id, ...}]} (default: reviews.json).",
    )
    args = p.parse_args()

    SGF_DIR.mkdir(exist_ok=True)
    source: Path = args.source
    d = json.loads(source.read_text())
    reviews = d["reviews"]
    print(f"Loaded {len(reviews)} reviews from {source.name}", flush=True)
    print(f"Output dir: {SGF_DIR}", flush=True)
    if args.limit:
        print(f"Limit: {args.limit}", flush=True)

    history: deque[float] = deque(maxlen=BUCKET_SIZE)
    counts = {"ok": 0, "skip": 0, "fail": 0}
    consecutive_429 = 0
    started = time.monotonic()
    fail_ids: list[int] = []

    for i, r in enumerate(reviews, 1):
        rid = r["id"]
        out_path = SGF_DIR / f"{rid}.sgf"
        result = download_one(rid, out_path, history)
        counts[result] += 1
        if result == "fail":
            fail_ids.append(rid)
            consecutive_429 += 1
            if consecutive_429 >= MAX_CONSECUTIVE_429:
                print(
                    f"\nAborting: {MAX_CONSECUTIVE_429} consecutive failures.",
                    flush=True,
                )
                break
        else:
            consecutive_429 = 0
        if i % 10 == 0 or args.limit and counts["ok"] + counts["skip"] >= args.limit:
            elapsed = time.monotonic() - started
            done = counts["ok"] + counts["skip"]
            print(
                f"  [{i}/{len(reviews)}] ok={counts['ok']} skip={counts['skip']} fail={counts['fail']} "
                f"elapsed={elapsed:.0f}s ({done / max(elapsed, 1):.2f}/s avg)",
                flush=True,
            )
        if args.limit and counts["ok"] + counts["skip"] >= args.limit:
            print(f"Hit --limit {args.limit}, stopping.", flush=True)
            break

    elapsed = time.monotonic() - started
    print(f"\nDone in {elapsed:.0f}s: {counts}", flush=True)
    if fail_ids:
        print(
            f"Failed ids ({len(fail_ids)}): {fail_ids[:30]}{'...' if len(fail_ids) > 30 else ''}",
            flush=True,
        )
    return 0 if not fail_ids else 1


if __name__ == "__main__":
    sys.exit(main())
