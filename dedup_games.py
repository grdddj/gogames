#!/usr/bin/env python3
"""Detect duplicate physical games across the corpus and pick a primary per group.

Two SGFs are considered "the same physical game" if either:
  (1) their full move sequences are identical (exact hash match), OR
  (2) their first PREFIX_LEN moves are identical AND their canonical player
      pairs match (catches partial-recording duplicates: one demo stops at move
      80, another goes to move 250 — same opening + same players ⇒ same game).

For each group, the primary is picked by:
  1. Most moves (most complete recording wins)
  2. Tiebreak: oldest `created` (closest to true game date)
  3. Tiebreak: real OGS id (positive) over synthetic local id (negative)

Outputs:
  - dedup_manifest.json: {"groups": [{primary, duplicates, ...}, ...], "primary_ids": [...]}
  - Summary printed to stdout

Does NOT delete or move SGFs. Read-only analysis.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
SGFS_DIR = ROOT / "sgfs"
OUT = ROOT / "dedup_manifest.json"

PREFIX_LEN = 30  # moves to consider as the "opening fingerprint"
MOVE_RE = re.compile(r";[BW]\[([a-z]{0,2})\]")
RANK_RE = re.compile(r"\s+\d{1,2}[dpk]\??\s*$", re.I)


def fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def canon_player(name: str) -> str:
    if not name:
        return ""
    n = RANK_RE.sub("", name.strip())
    n = re.sub(r"[,\.()]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = fold(n).lower()
    return " ".join(sorted(n.split()))


def player_pair(pb: str, pw: str) -> tuple[str, str]:
    return tuple(sorted((canon_player(pb), canon_player(pw))))  # type: ignore[return-value]


def hash_moves(moves: list[str]) -> str:
    return hashlib.sha1("".join(moves).encode()).hexdigest()


def main() -> int:
    # Build review metadata index
    review_meta: dict[int, dict] = {}
    for rfile in sorted(ROOT.glob("reviews_*.json")):
        d = json.loads(rfile.read_text())
        for r in d["reviews"]:
            review_meta[r["id"]] = r

    # Parse every SGF
    print(f"Parsing {len(review_meta)} SGFs...", flush=True)
    rows: list[dict] = []
    for path in sorted(SGFS_DIR.glob("*.sgf")):
        try:
            rid = int(path.stem)
        except ValueError:
            continue
        if rid not in review_meta:
            continue
        raw = path.read_text(errors="replace")
        moves = MOVE_RE.findall(raw)
        meta = review_meta[rid]
        p = meta.get("players") or {}
        pb = (p.get("black") or {}).get("username") or ""
        pw = (p.get("white") or {}).get("username") or ""
        rows.append(
            {
                "id": rid,
                "created": meta.get("created"),
                "name": meta.get("name") or "",
                "recorder_group": meta.get("recorder_group"),
                "recorder": meta.get("recorder"),
                "n_moves": len(moves),
                "full_hash": hash_moves(moves),
                "prefix_hash": hash_moves(moves[:PREFIX_LEN])
                if len(moves) >= PREFIX_LEN
                else None,
                "pair": player_pair(pb, pw),
                "pb": pb,
                "pw": pw,
            }
        )

    # Phase 1: group by exact full-move hash. SGFs with 0 moves are ungroupable
    # (they all share an empty-string hash), so each becomes its own singleton.
    print("Phase 1: exact full-move hash grouping...", flush=True)
    by_full: dict[str, list[dict]] = defaultdict(list)
    empty_singletons: list[dict] = []
    for r in rows:
        if r["n_moves"] == 0:
            empty_singletons.append(r)
        else:
            by_full[r["full_hash"]].append(r)

    groups: list[list[dict]] = list(by_full.values())
    groups.extend([r] for r in empty_singletons)

    # Phase 2: merge groups that share (prefix_hash, pair) — partial-recording dupes
    print("Phase 2: prefix-hash + player-pair fuzzy grouping...", flush=True)
    # Build a lookup: (prefix_hash, pair) → list of group indices
    pp_to_groups: dict[tuple, set[int]] = defaultdict(set)
    for gi, grp in enumerate(groups):
        for m in grp:
            if m["prefix_hash"] is None or not (m["pair"][0] and m["pair"][1]):
                continue
            pp_to_groups[(m["prefix_hash"], m["pair"])].add(gi)

    # Union-find merge of group indices that share any (prefix_hash, pair) key
    parent = list(range(len(groups)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for indices in pp_to_groups.values():
        if len(indices) > 1:
            it = iter(indices)
            first = next(it)
            for other in it:
                union(first, other)

    # Collapse merged groups
    merged: dict[int, list[dict]] = defaultdict(list)
    for gi, grp in enumerate(groups):
        merged[find(gi)].extend(grp)
    final_groups = list(merged.values())

    # Pick primary in each group
    def primary_key(r: dict) -> tuple:
        # Sort key that puts the best candidate FIRST.
        # Most moves first → use negative count.
        # Then oldest `created` (None last).
        # Then real OGS id (positive) before synthetic (negative).
        created = r["created"] or "9999"
        return (-r["n_moves"], created, -1 if r["id"] > 0 else 0, abs(r["id"]))

    # Build manifest
    out_groups = []
    primary_ids: list[int] = []
    duplicate_ids: list[int] = []
    multi_groups = 0
    for grp in final_groups:
        grp_sorted = sorted(grp, key=primary_key)
        primary = grp_sorted[0]
        dups = grp_sorted[1:]
        if dups:
            multi_groups += 1
        primary_ids.append(primary["id"])
        duplicate_ids.extend(d["id"] for d in dups)
        out_groups.append(
            {
                "primary": {
                    "id": primary["id"],
                    "n_moves": primary["n_moves"],
                    "recorder": primary["recorder"],
                    "name": primary["name"],
                    "created": primary["created"],
                },
                "duplicates": [
                    {
                        "id": d["id"],
                        "n_moves": d["n_moves"],
                        "recorder": d["recorder"],
                        "name": d["name"],
                        "created": d["created"],
                    }
                    for d in dups
                ],
                "pair": list(primary["pair"]),
            }
        )

    OUT.write_text(
        json.dumps(
            {
                "primary_ids": primary_ids,
                "duplicate_ids": duplicate_ids,
                "groups": out_groups,
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    print()
    print("=== Summary ===")
    print(f"  Total reviews:           {len(rows)}")
    print(f"  Distinct physical games: {len(final_groups)}")
    print(f"  Duplicate groups (≥2):   {multi_groups}")
    print(f"  Total duplicates dropped: {len(duplicate_ids)}")
    print(f"  Wrote {OUT.name}")

    # Top-N largest duplicate groups
    print("\n=== Top duplicate groups (most copies of same game) ===")
    out_groups.sort(key=lambda g: -len(g["duplicates"]))
    for g in out_groups[:8]:
        if not g["duplicates"]:
            break
        print(f"  {1 + len(g['duplicates'])}× same game: {g['pair']}")
        print(
            f"    primary  id={g['primary']['id']:<10} moves={g['primary']['n_moves']:<3} [{g['primary']['recorder']}] {g['primary']['name']!r}"
        )
        for d in g["duplicates"]:
            print(
                f"    dup      id={d['id']:<10} moves={d['n_moves']:<3} [{d['recorder']}] {d['name']!r}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
