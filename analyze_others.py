#!/usr/bin/env python3
"""Compare SGFs in ./others/ against the OGS-fetched corpus.

For each SGF in others/, classify as:
  - 'ogs_id_match'        : filename like demo-<id>.sgf and that id is in our reviews
  - 'content_match'       : move sequence matches an SGF we already have in sgfs/
  - 'fuzzy_match'         : same (date+player_pair) as something we have, but moves differ
  - 'unique'              : nothing matches — genuinely new game

Outputs a summary plus a `others_unique.json` listing the unique entries.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
OTHERS = ROOT / "others"
SGFS = ROOT / "sgfs"
REVIEWS_FILES = sorted(ROOT.glob("reviews_*.json"))

TAG_RE = re.compile(r"([A-Z]+)\[((?:[^\]\\]|\\.)*)\]")
MOVE_RE = re.compile(r";[BW]\[([a-z]{0,2})\]")
RANK_RE = re.compile(r"\s+\d{1,2}[dpk]\??\s*$", re.I)


def fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def canonical_player(name: str) -> str:
    if not name:
        return ""
    n = RANK_RE.sub("", name.strip())
    n = re.sub(r"[,\.]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = fold(n).lower()
    return " ".join(sorted(n.split()))


def parse_sgf(path: Path) -> dict:
    raw = path.read_text(errors="replace")
    head = raw[:4000]
    tags = {}
    for k, v in TAG_RE.findall(head):
        if k in ("PB", "PW", "GN", "DT", "EV", "PC", "RE", "KM"):
            tags.setdefault(k, v)
    # Move sequence hash (only the coordinate strings, no comments/metadata)
    moves = MOVE_RE.findall(raw)
    move_hash = hashlib.sha1("".join(moves).encode()).hexdigest()
    return {
        "path": str(path),
        "pb": tags.get("PB", ""),
        "pw": tags.get("PW", ""),
        "gn": tags.get("GN", ""),
        "dt": tags.get("DT", ""),
        "moves": len(moves),
        "move_hash": move_hash,
    }


def player_pair(pb: str, pw: str) -> tuple[str, str]:
    a, b = canonical_player(pb), canonical_player(pw)
    return tuple(sorted((a, b)))  # type: ignore[return-value]


def main() -> int:
    # 1. Build index of our existing SGFs (the canonical OGS-downloaded set)
    print("Indexing existing sgfs/...", flush=True)
    sgfs_by_id: dict[int, dict] = {}
    sgfs_by_hash: dict[str, list[int]] = defaultdict(list)
    sgfs_by_pair_date: dict[tuple, list[int]] = defaultdict(list)

    # Cross-reference with reviews_*.json to get demo `created` date for each id
    review_meta: dict[int, dict] = {}
    for rfile in REVIEWS_FILES:
        d = json.loads(rfile.read_text())
        for r in d["reviews"]:
            review_meta[r["id"]] = r

    for path in sorted(SGFS.glob("*.sgf")):
        try:
            rid = int(path.stem)
        except ValueError:
            continue
        info = parse_sgf(path)
        info["id"] = rid
        info["created"] = (review_meta.get(rid, {}).get("created") or "")[:10]
        sgfs_by_id[rid] = info
        sgfs_by_hash[info["move_hash"]].append(rid)
        if info["pb"] and info["pw"]:
            sgfs_by_pair_date[
                (player_pair(info["pb"], info["pw"]), info["created"])
            ].append(rid)
    print(f"  indexed {len(sgfs_by_id)} sgfs ({len(sgfs_by_hash)} unique move-hashes)")

    # 2. Walk others/
    others_files = sorted(OTHERS.rglob("*.sgf"))
    print(f"\nScanning {len(others_files)} files in others/ ...\n", flush=True)
    counts = {
        "ogs_id_match": 0,
        "ogs_known": 0,
        "content_match": 0,
        "fuzzy_match": 0,
        "unique": 0,
        "empty": 0,
    }
    classified: list[dict] = []
    for path in others_files:
        info = parse_sgf(path)
        info["folder"] = path.parent.name
        info["fname"] = path.name
        kind = None
        match_to: list[int] = []

        # Try filename id match first
        m = re.search(r"(\d{6,})", path.stem)
        if m:
            rid = int(m.group(1))
            if rid in sgfs_by_id:
                kind = "ogs_id_match"
                match_to = [rid]
            elif rid in review_meta:
                # We know about this review (it's in some reviews_*.json), but
                # we haven't downloaded its SGF yet. Still counts as "known".
                kind = "ogs_known"
                match_to = [rid]

        # Content hash match
        if not kind:
            if info["moves"] == 0:
                kind = "empty"
            elif info["move_hash"] in sgfs_by_hash:
                kind = "content_match"
                match_to = sgfs_by_hash[info["move_hash"]]

        # Fuzzy: same (player_pair, date) — date may be in DT tag
        if not kind:
            dt = info["dt"][:10]
            pair = player_pair(info["pb"], info["pw"])
            if pair[0] and pair[1] and dt:
                if (pair, dt) in sgfs_by_pair_date:
                    kind = "fuzzy_match"
                    match_to = sgfs_by_pair_date[(pair, dt)]

        if not kind:
            kind = "unique"
        info["kind"] = kind
        info["matches"] = match_to
        counts[kind] += 1
        classified.append(info)

    # 3. Report
    print("=== Summary ===")
    for k in ("ogs_id_match", "ogs_known", "content_match", "fuzzy_match", "unique", "empty"):
        print(f"  {k:>14}: {counts[k]:>3}")

    # Per-folder breakdown
    print("\n=== Per-folder breakdown ===")
    folder_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for info in classified:
        folder_counts[info["folder"]][info["kind"]] += 1
    for folder, kinds in sorted(folder_counts.items()):
        total = sum(kinds.values())
        parts = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()) if v)
        print(f"  {folder:<14} ({total:>2}): {parts}")

    # Print unique games in detail
    uniques = [i for i in classified if i["kind"] == "unique"]
    print(f"\n=== {len(uniques)} unique (not in our OGS corpus) ===")
    for u in uniques:
        dt = (u["dt"] or "")[:10] or "?"
        print(
            f"  [{u['folder']}/{u['fname']}]  {dt}  {u['pb']!r:<28} vs {u['pw']!r:<28}  moves={u['moves']:<3}  GN={u['gn']!r}"
        )

    # Save unique list
    out = {
        "summary": counts,
        "unique": [
            {
                "folder": u["folder"],
                "fname": u["fname"],
                "path": u["path"],
                "pb": u["pb"],
                "pw": u["pw"],
                "dt": u["dt"],
                "gn": u["gn"],
                "moves": u["moves"],
            }
            for u in uniques
        ],
    }
    Path("others_unique.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print("\nWrote others_unique.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
