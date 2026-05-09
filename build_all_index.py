#!/usr/bin/env python3
"""Build a unified all_games.json from all reviews_*.json sources.

Filters out:
  - Empty reviews (_empty: true)
  - Duplicates (only keeps primary_ids from dedup_manifest.json)

Output shape matches what build_named_folder.py expects (flat black/white).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "all_games.json"


def main() -> int:
    manifest = json.loads((ROOT / "dedup_manifest.json").read_text())
    primary_ids: set[int] = set(manifest["primary_ids"])

    rows: list[dict] = []
    for rfile in sorted(ROOT.glob("reviews_*.json")):
        d = json.loads(rfile.read_text())
        for r in d["reviews"]:
            if r.get("_empty"):
                continue
            if r["id"] not in primary_ids:
                continue
            p = r.get("players") or {}
            rows.append(
                {
                    "id": r["id"],
                    "created": r.get("created"),
                    "name": r.get("name") or "",
                    "black": (p.get("black") or {}).get("username")
                    or r.get("black")
                    or "",
                    "white": (p.get("white") or {}).get("username")
                    or r.get("white")
                    or "",
                    "recorder": r.get("recorder"),
                    "recorder_group": r.get("recorder_group"),
                }
            )

    rows.sort(key=lambda r: (r["created"] or "", r["id"]))
    OUT.write_text(
        json.dumps({"count": len(rows), "reviews": rows}, indent=2, ensure_ascii=False)
    )
    print(f"Wrote {len(rows)} unique non-empty games to {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
