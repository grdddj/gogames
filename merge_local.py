#!/usr/bin/env python3
"""Merge truly-unique SGFs from others/ into the main corpus.

For each entry classified as `unique` by analyze_others.py:
  1. Compute a synthetic id (negative integer derived from move_hash, so it's
     stable across re-runs and obviously distinguishable from real OGS ids).
  2. Copy the SGF to sgfs/<synthetic_id>.sgf.
  3. Append a review record (mimicking the OGS reviews_*.json shape) to
     reviews_local.json with recorder_group='local' and recorder=<folder name>.

Re-running is idempotent: existing files/entries are detected via synthetic id.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
OTHERS_UNIQUE = ROOT / "others_unique.json"
SGFS_DIR = ROOT / "sgfs"
OUT_FILE = ROOT / "reviews_local.json"

TAG_RE = re.compile(r"([A-Z]+)\[((?:[^\]\\]|\\.)*)\]")
RANK_RE = re.compile(r"\s+\d{1,2}[dpk]\??\s*$", re.I)


def fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def synthetic_id(move_hash: str) -> int:
    # Negative int from first 7 hex chars of the hash → stable, distinct from OGS ids.
    return -int(move_hash[:7], 16)


def parse_full_tags(path: Path) -> dict:
    raw = path.read_text(errors="replace")
    head = raw[:4000]
    tags: dict[str, str] = {}
    for k, v in TAG_RE.findall(head):
        tags.setdefault(k, v)
    moves = re.findall(r";[BW]\[([a-z]{0,2})\]", raw)
    return {
        "tags": tags,
        "move_count": len(moves),
        "move_hash": hashlib.sha1("".join(moves).encode()).hexdigest(),
    }


def to_iso(dt_str: str) -> str | None:
    """Try to parse a free-form SGF DT field into an ISO datetime string."""
    if not dt_str:
        return None
    s = dt_str.strip().split(",")[0].split(";")[0].strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def main() -> int:
    SGFS_DIR.mkdir(exist_ok=True)
    others = json.loads(OTHERS_UNIQUE.read_text())
    uniques = others["unique"]

    new_records: list[dict] = []
    copied = skipped = 0

    for entry in uniques:
        src = Path(entry["path"])
        info = parse_full_tags(src)
        sid = synthetic_id(info["move_hash"])
        dst = SGFS_DIR / f"{sid}.sgf"
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            skipped += 1
        else:
            shutil.copy2(src, dst)
            copied += 1

        # Build a review record close to the OGS shape so downstream search code works.
        tags = info["tags"]
        pb = tags.get("PB", "")
        pw = tags.get("PW", "")
        # Strip rank suffixes for username, mirror what OGS would store
        pb_user = RANK_RE.sub("", pb).strip().rstrip("()").strip()
        pw_user = RANK_RE.sub("", pw).strip().rstrip("()").strip()
        # Also strip trailing " (Nd)" / " (1p)" parens that some local SGFs use
        pb_user = re.sub(r"\s*\([^)]*\)\s*$", "", pb_user).strip()
        pw_user = re.sub(r"\s*\([^)]*\)\s*$", "", pw_user).strip()

        # Date from DT tag or fall back to None
        created = to_iso(tags.get("DT", ""))

        rec = {
            "id": sid,
            "name": tags.get("GN", "") or src.stem,
            "created": created,
            "updated": created,
            "private": False,
            "owner": {"id": None, "username": entry["folder"]},
            "controller": {"id": None, "username": entry["folder"]},
            "game": {"id": 0, "width": 19, "height": 19},
            "players": {
                "black": {
                    "id": 0,
                    "username": pb_user,
                    "ranking": None,
                    "professional": False,
                },
                "white": {
                    "id": 0,
                    "username": pw_user,
                    "ranking": None,
                    "professional": False,
                },
            },
            "auth": None,
            "review_chat_auth": None,
            "review_voice_auth": None,
            "recorder": entry["folder"],
            "recorder_id": None,
            "recorder_group": "local",
            "_local_path": str(src),  # keep provenance
            "_move_count": info["move_count"],
            "_move_hash": info["move_hash"],
        }
        new_records.append(rec)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "group": "local",
        "accounts": [
            {
                "id": None,
                "username": "(local files)",
                "found": True,
                "review_count": len(new_records),
            }
        ],
        "total": len(new_records),
        "reviews": new_records,
    }
    OUT_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(
        f"Copied {copied} files (skipped {skipped} already-present), wrote {len(new_records)} records to {OUT_FILE.name}"
    )

    # Quick stats
    from collections import Counter

    folders = Counter(r["recorder"] for r in new_records)
    dated = sum(1 for r in new_records if r["created"])
    print(f"\nWith DT date: {dated}/{len(new_records)}")
    print("\nBy folder:")
    for f, n in sorted(folders.items(), key=lambda x: -x[1]):
        print(f"  {n:>3}  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
