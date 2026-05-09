#!/usr/bin/env python3
"""Move empty (0-move) SGFs to sgfs/empty/ and flag their metadata.

Actions:
  1. Identify SGFs in sgfs/ that have 0 move nodes (;B[..] / ;W[..]).
  2. Move them to sgfs/empty/<id>.sgf (preserved, just out of the main set).
  3. Annotate each reviews_*.json: set "_empty": true on each affected record.
  4. Rewrite podpera_games.json (and its dedup) to exclude empties.
  5. Drop the corresponding files from podpera-sgfs/, podpera-sgfs-named/,
     and podpera-sgfs-named-dedup/.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
SGFS_DIR = ROOT / "sgfs"
EMPTY_DIR = SGFS_DIR / "empty"

MOVE_RE = re.compile(r";[BW]\[([a-z]{0,2})\]")


def is_empty_sgf(path: Path) -> bool:
    return len(MOVE_RE.findall(path.read_text(errors="replace"))) == 0


def main() -> int:
    EMPTY_DIR.mkdir(exist_ok=True)

    # Find empties
    empty_ids: set[int] = set()
    for path in SGFS_DIR.glob("*.sgf"):
        try:
            rid = int(path.stem)
        except ValueError:
            continue
        if is_empty_sgf(path):
            empty_ids.add(rid)
    print(f"Found {len(empty_ids)} empty SGFs")

    # Move SGFs
    moved = 0
    for rid in empty_ids:
        src = SGFS_DIR / f"{rid}.sgf"
        dst = EMPTY_DIR / f"{rid}.sgf"
        if src.exists():
            shutil.move(str(src), str(dst))
            moved += 1
    print(f"Moved {moved} files to {EMPTY_DIR}")

    # Flag in reviews_*.json
    flagged = 0
    for rfile in sorted(ROOT.glob("reviews_*.json")):
        d = json.loads(rfile.read_text())
        changed = False
        for r in d["reviews"]:
            if r["id"] in empty_ids and not r.get("_empty"):
                r["_empty"] = True
                flagged += 1
                changed = True
        if changed:
            rfile.write_text(json.dumps(d, indent=2, ensure_ascii=False))
            print(f"  flagged in {rfile.name}")
    print(f"Flagged {flagged} review records with _empty=true")

    # Rewrite podpera_games.json (drop empties), update dedup version too
    for pfile in (ROOT / "podpera_games.json", ROOT / "podpera_games_dedup.json"):
        if not pfile.exists():
            continue
        d = json.loads(pfile.read_text())
        before = len(d["reviews"])
        d["reviews"] = [r for r in d["reviews"] if r["id"] not in empty_ids]
        d["count"] = len(d["reviews"])
        pfile.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        print(
            f"  {pfile.name}: {before} -> {d['count']} (dropped {before - d['count']})"
        )

    # Drop empties from podpera-sgfs/, podpera-sgfs-named/, podpera-sgfs-named-dedup/
    for folder in (
        ROOT / "podpera-sgfs",
        ROOT / "podpera-sgfs-named",
        ROOT / "podpera-sgfs-named-dedup",
    ):
        if not folder.exists():
            continue
        removed = 0
        for path in folder.glob("*.sgf"):
            # Two filename styles: "<id>.sgf" or "<...>_<id>.sgf"
            m = re.search(r"(-?\d+)\.sgf$", path.name)
            if not m:
                continue
            rid = int(m.group(1))
            if rid in empty_ids:
                path.unlink()
                removed += 1
        print(f"  {folder.name}: removed {removed} files")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
