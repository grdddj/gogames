#!/usr/bin/env python3
"""Collapse pandanet/ match folders to drop the date.

Old: pandanet/<season>/R03_2020-11-10_Serbia-vs-Czechia/<board>.sgf
New: pandanet/<season>/R03_Serbia-vs-Czechia/<board>.sgf

If multiple old folders collapse into the same new name (rare — only happens if
the same teams play the same round on different dates), files are merged. On
filename collision, an `_2`, `_3`, ... suffix is appended.

Idempotent: if a folder is already in the new shape, it is left alone.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent / "pandanet"

# Match: R<num>_<date>_<rest>   OR   R<num>_unknown-date_<rest>
DATE_PATTERN = re.compile(r"^(R\d+)_(?:\d{4}-\d{2}-\d{2}|unknown-date)_(.+)$")


def main() -> int:
    if not ROOT.exists():
        print(f"No {ROOT} folder")
        return 1

    moved = collisions = unchanged = 0
    for season_dir in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        # Group existing match folders by their new name
        regrouped: dict[str, list[Path]] = {}
        for d in sorted(p for p in season_dir.iterdir() if p.is_dir()):
            m = DATE_PATTERN.match(d.name)
            if m:
                new_name = f"{m.group(1)}_{m.group(2)}"
            else:
                new_name = d.name  # already in new shape
            regrouped.setdefault(new_name, []).append(d)

        for new_name, sources in regrouped.items():
            new_dir = season_dir / new_name
            # If only one source and it's already named correctly, skip
            if len(sources) == 1 and sources[0].name == new_name:
                unchanged += 1
                continue
            new_dir.mkdir(exist_ok=True)
            for src in sources:
                if src == new_dir:
                    continue
                for sgf in src.glob("*.sgf"):
                    dst = new_dir / sgf.name
                    if dst.exists():
                        # Collision: add suffix
                        n = 2
                        stem, ext = sgf.stem, sgf.suffix
                        while (new_dir / f"{stem}_{n}{ext}").exists():
                            n += 1
                        dst = new_dir / f"{stem}_{n}{ext}"
                        collisions += 1
                    shutil.move(str(sgf), str(dst))
                    moved += 1
                # Remove now-empty source folder (skip if it equals new_dir)
                try:
                    src.rmdir()
                except OSError:
                    pass

    print(
        f"Moved {moved} files, {collisions} collisions resolved with suffix, {unchanged} folders untouched"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
