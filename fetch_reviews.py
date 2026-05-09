#!/usr/bin/env python3
"""Fetch all OGS reviews/demos owned by a named group of recorder accounts.

Writes reviews_<group>.json with the full raw records, each tagged with the
recorder username/id and the group name.

Usage:
    uv run python fetch_reviews.py --group egf
    uv run python fetch_reviews.py --group czbaron
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Recorder accounts. Comments document expected username; actual username is
# fetched from the API and used as the `recorder` tag on each review.
ACCOUNT_GROUPS: dict[str, list[int]] = {
    "egf": [
        656132,  # EGF
        656151,  # EGF1
        656152,  # EGF2
        # 656153 exists but belongs to user "hessler.dan", not an EGF account
        656154,  # EGF3
        656155,  # EGF4
        656156,  # EGF5
        656157,  # EGF6
    ],
    "czbaron": [
        1265231,  # CZBaron1
        1469643,  # CZBaron2
        1471422,  # CZBaron3
    ],
    "cago": [
        1459103,  # CAGo1
        1459104,  # CAGo2
        1459105,  # CAGo3
        1459107,  # CAGo4 (1459106 skipped — likely unrelated user)
    ],
    "dmedak": [
        687324,  # dmedak (Damir Medak — prolific EU tournament demoer)
    ],
}

API_BASE = "https://online-go.com/api/v1"
ROOT = Path(__file__).parent
CACHE_DIR = ROOT / ".cache" / "reviews_per_account"
SLEEP_BETWEEN_REQUESTS_S = 1.0
MAX_RETRIES = 8
USER_AGENT = "ogs-review-archiver/0.1 (+local research script)"


def http_get_json(url: str) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            req = Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            last_exc = e
            if e.code in (429, 500, 502, 503, 504):
                backoff = min(60, 5 * (2**attempt))
                print(
                    f"    HTTP {e.code} on {url}; retrying in {backoff}s",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                continue
            raise
        except URLError as e:
            last_exc = e
            backoff = 2**attempt
            print(f"    network error {e}; retrying in {backoff}s", file=sys.stderr)
            time.sleep(backoff)
    assert last_exc is not None
    raise last_exc


def fetch_player(
    player_id: int, group: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return (player_info, all_reviews). player_info is None if account doesn't exist.

    Uses a per-account cache file so re-runs skip already-fetched accounts.
    """
    cache_file = CACHE_DIR / f"{player_id}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text())
        username = cached["player"].get("username")
        for r in cached["reviews"]:
            r["recorder"] = username
            r["recorder_id"] = player_id
            r["recorder_group"] = group
        print(
            f"  player {player_id} ({username}): "
            f"loaded {len(cached['reviews'])} reviews from cache"
        )
        return cached["player"], cached["reviews"]

    try:
        player = http_get_json(f"{API_BASE}/players/{player_id}/")
    except HTTPError as e:
        if e.code == 404:
            print(f"  player {player_id}: not found, skipping")
            return None, []
        raise

    username = player.get("username", f"id{player_id}")
    print(f"  player {player_id} ({username}): fetching reviews...")

    reviews: list[dict[str, Any]] = []
    url: str | None = f"{API_BASE}/reviews?owner_id={player_id}"
    page = 0
    while url:
        page += 1
        data = http_get_json(url)
        batch = data.get("results", [])
        for r in batch:
            r["recorder"] = username
            r["recorder_id"] = player_id
            r["recorder_group"] = group
        reviews.extend(batch)
        total = data.get("count")
        print(f"    page {page}: +{len(batch)} (running total {len(reviews)}/{total})")
        url = data.get("next")
        time.sleep(SLEEP_BETWEEN_REQUESTS_S)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({"player": player, "reviews": reviews}, ensure_ascii=False)
    )
    return player, reviews


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--group", required=True, choices=sorted(ACCOUNT_GROUPS.keys()))
    args = p.parse_args()

    group: str = args.group
    player_ids = ACCOUNT_GROUPS[group]
    out_file = ROOT / f"reviews_{group}.json"

    print(f"Group '{group}': fetching reviews for {len(player_ids)} accounts...")
    all_reviews: list[dict[str, Any]] = []
    accounts_summary: list[dict[str, Any]] = []

    for pid in player_ids:
        player, reviews = fetch_player(pid, group)
        if player is None:
            accounts_summary.append(
                {"id": pid, "username": None, "found": False, "review_count": 0}
            )
            continue
        accounts_summary.append(
            {
                "id": pid,
                "username": player.get("username"),
                "found": True,
                "review_count": len(reviews),
            }
        )
        all_reviews.extend(reviews)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "group": group,
        "accounts": accounts_summary,
        "total": len(all_reviews),
        "reviews": all_reviews,
    }

    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    found_n = sum(1 for a in accounts_summary if a["found"])
    print(f"\nWrote {len(all_reviews)} reviews from {found_n} accounts to {out_file}")
    for a in accounts_summary:
        marker = "ok" if a["found"] else "MISSING"
        print(
            f"  [{marker:>7}] {a['id']} {a['username'] or '-':<10} {a['review_count']:>4} reviews"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
