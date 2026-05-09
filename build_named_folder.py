#!/usr/bin/env python3
"""Build a renamed copy of podpera-sgfs/ with descriptive filenames.

Filename scheme A:  YYYY-MM-DD_<TourneyKey>_<Round>_<id>.sgf

Each output file's mtime is set to the demo's `created` timestamp from the
OGS API so that any tool sorting by file date (e.g. SmartGo) shows the games
in chronological order.

Source of truth: podpera_games.json + sgfs/<id>.sgf
Output: podpera-sgfs-named/<descriptive>.sgf
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import sys

ROOT = Path(__file__).parent
SRC_DIR = ROOT / "sgfs"
# Optional CLI: build_named_folder.py [<index.json> <out-dir>]
INDEX = Path(sys.argv[1]) if len(sys.argv) > 2 else ROOT / "podpera_games.json"
DST_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "podpera-sgfs-named"


def ascii_fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def slugify(s: str) -> str:
    s = ascii_fold(s)
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s or "x"


def find_round(name: str) -> str | None:
    """Best-effort round token from a free-text name."""
    n = name.strip()
    # Decimal round (EGC weekend "round 4.19" → R4)
    m = re.search(r"\bround\s+(\d+)\.\d+", n, re.I)
    if m:
        return f"R{m.group(1)}"
    # "8th EGP final 2024, places 5-8 B3" — places token
    m = re.search(r"places?\s+(\d+)\s*-\s*(\d+)", n, re.I)
    if m:
        return f"P{m.group(1)}-{m.group(2)}"
    # 1/4 finale, 3/4 places
    if re.search(r"\b1/4\b|quarter[- ]?final", n, re.I):
        return "QF"
    if re.search(r"semi[- ]?final", n, re.I):
        return "SF"
    if re.search(r"\b3/4\b", n, re.I):
        return "3-4"
    if re.search(r"\bfor\s+5th\s+place\b", n, re.I):
        return "5th"
    # Match N (Play with Brain)
    m = re.search(r"\bmatch\s*(\d+)", n, re.I)
    if m:
        return f"M{m.group(1)}"
    # Final N (Czech Go Baron series)
    m = re.search(r"\bfinal[s]?\s*(\d+)", n, re.I)
    if m:
        return f"F{m.group(1)}"
    # Game N
    m = re.search(r"\bgame\s*(\d+)", n, re.I)
    if m:
        return f"G{m.group(1)}"
    # R<n>, Round <n>, Rd <n>
    m = re.search(r"\bR\s*(\d+)\b", n)
    if m:
        return f"R{m.group(1)}"
    m = re.search(r"\bround\s+(\d+)", n, re.I)
    if m:
        return f"R{m.group(1)}"
    m = re.search(r"\bRd\s*(\d+)", n, re.I)
    if m:
        return f"R{m.group(1)}"
    m = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+round", n, re.I)
    if m:
        return f"R{m.group(1)}"
    # Plain "Final" / "Finals" without number
    if re.search(r"\bfinal[s]?\b", n, re.I):
        return "Final"
    return None


# Handlers in priority order. Each returns (tourney_key, round_or_None).
# tourney_key embeds the year so different editions sort apart.
def h_czech_baron(name: str, year: str) -> tuple[str, str | None]:
    return f"CZ-Baron-{year}", find_round(name)


def h_handler(name: str, created_year: str) -> tuple[str, str | None]:
    n = name.strip()

    # Czech Go Baron / GoBaron / Go Baron — pull explicit year if present, else use demo year
    if re.search(r"\b(?:czech\s+)?(?:go\s*)?baron\b", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"CZ-Baron-{m.group(1) if m else created_year}", find_round(n)

    # Czech Championship YYYY  (round missing in source — caller will fill with date order)
    m = re.match(r"^Czech\s+Championship\s+(\d{4})\s*$", n, re.I)
    if m:
        return f"CZ-Champ-{m.group(1)}", None

    # CEGO Grand Slam (online)
    if (
        re.search(r"\bCEGO\b", n, re.I)
        or re.search(r"european\s+grand\s+slam", n, re.I)
        or re.search(r"^Grand\s+Slam\s+(\d{4})", n, re.I)
    ):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"CEGO-GS-{m.group(1) if m else created_year}", find_round(n)

    # EGF Pro Qualification — many name variants:
    # "EGF ProQuali", "EPQ", "EPQ 2021", "European Pro Qualification",
    # "EGF Pro Quali(fication)", "5th EGF PRO Qualification 2019", "Pro Quali"
    if re.search(
        r"ProQuali|\bEPQ\b|Pro\s+Quali|European\s+Pro\s+Qualification|EGF\s+PRO\s+Qualif",
        n,
        re.I,
    ):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"EGF-ProQ-{m.group(1) if m else created_year}", find_round(n)

    # Croatian Open
    if re.search(r"Croatian\s+Open", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"CroatianOpen-{m.group(1) if m else created_year}", find_round(n)

    # Velika Gorica Grand Prix
    if re.search(r"Velika\s+Gorica", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"VelikaGorica-{m.group(1) if m else created_year}", find_round(n)

    # Lanke Cup Vienna Open
    if re.search(r"Lanke\s+Cup", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"LankeVienna-{m.group(1) if m else created_year}", find_round(n)

    # Vienna Open (non-Lanke)
    if re.search(r"Vienna\s+Open", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"ViennaOpen-{m.group(1) if m else created_year}", find_round(n)

    # 53rd Prague Tournament — extend existing Prague rule for non-"Go Tour" naming
    m = re.match(r"^\s*(\d+)\w*\s+Prague\s+(?:Go\s+)?Tour", n, re.I)
    if m:
        return f"Prague-{m.group(1)}", find_round(n)
    m = re.match(r"^\s*(\d+)\w*\s+Prague\s+Tournament", n, re.I)
    if m:
        return f"Prague-{m.group(1)}", find_round(n)

    # EGF / European Grand Prix Finale (8th EGP Finale, EGF Grand Prix Finals, Grand Prix Finale Group)
    if re.search(
        r"\bEGP\b\s+(?:Finale|final)|EGF\s+Grand\s+Prix|Grand\s+Prix\s+(?:Finale|Finals)",
        n,
        re.I,
    ):
        m = re.search(r"\b(20\d{2})\b", n)
        # 8th EGP Finale 2024 → 2024 final
        year = m.group(1) if m else created_year
        return f"EGPF-Final-{year}", find_round(n)

    # EGPF YYYY / European Grand Prix R<n> (qualifier games).
    # Note: "EGPF2022" has no \b between EGPF and the year, so use EGPF\d* instead.
    if re.search(r"\bEGPF\d*\b|European\s+Grand\s+Prix", n, re.I):
        m = re.search(r"(?:EGPF|\b)(20\d{2})\b", n)
        return f"EGPF-{m.group(1) if m else created_year}", find_round(n)

    # EGC weekend / open
    if re.search(r"\bEGC\b", n, re.I) or re.search(r"\bweekend\b", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        year = m.group(1) if m else created_year
        flavor = "Open" if re.search(r"\bopen\b", n, re.I) else "Weekend"
        return f"EGC-{flavor}-{year}", find_round(n)

    # Pair European Tournament Champion (PETC)
    if re.search(r"\bPETC\b", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"PETC-{m.group(1) if m else created_year}", find_round(n)

    # European Championship (non-EGC)
    if re.search(r"european\s+championship", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"EuroCh-{m.group(1) if m else created_year}", find_round(n)

    # TIGGRE EllieCup
    if re.search(r"EllieCup", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"EllieCup-{m.group(1) if m else created_year}", find_round(n)

    # "Nth Prague Go Tour(nament)"
    m = re.match(r"^\s*(\d+)\w*\s+Prague\s+Go\s+Tour", n, re.I)
    if m:
        return f"Prague-{m.group(1)}", find_round(n)

    # Brno Open
    if re.search(r"\bBrno\b", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"Brno-{m.group(1) if m else created_year}", find_round(n)

    # Blansko (XVIII. Blansko Tournament)
    if re.search(r"Blansko", n, re.I):
        m = re.match(r"^\s*([IVX]+)\.\s*Blansko", n, re.I)
        edition = m.group(1) if m else created_year
        return f"Blansko-{edition}", find_round(n)

    # Sokolov
    if re.search(r"Sokolov", n, re.I):
        m = re.match(r"^\s*(\d+)\w*\s+Sokolov", n, re.I)
        edition = m.group(1) if m else created_year
        return f"Sokolov-{edition}", find_round(n)

    # Olomouc
    if re.search(r"Olomouc", n, re.I):
        return f"Olomouc-{created_year}", find_round(n)

    # Hungarian Open
    if re.search(r"Hungarian\s+Open", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"HUN-Open-{m.group(1) if m else created_year}", find_round(n)

    # Czech league of teams
    if re.search(r"liga\s+druzstev|česká\s+liga", ascii_fold(n), re.I) or re.search(
        r"liga\s+druzstev", ascii_fold(n), re.I
    ):
        m = re.search(r"(\d{4})", n)
        return f"CZ-Liga-{m.group(1) if m else created_year}", find_round(n)

    # Play with Brain
    if re.search(r"Play\s+with\s+Brain", n, re.I):
        return f"PlayWithBrain-{created_year}", find_round(n)

    # IZIS transcripts
    if re.search(r"IZIS2OGS", n, re.I):
        m = re.search(r"transcript\s+of\s+game\s+(\d+)", n, re.I)
        return f"IZIS-{created_year}", f"g{m.group(1)}" if m else None

    # Fallback: slug of the whole name (32-char cap)
    return slugify(n)[:32], None


def categorize_with_recorder_fallback(
    name: str, created: str | None, recorder: str | None
) -> tuple[str, str | None]:
    """Like categorize() but if the result is a slugified fallback >= 16 chars,
    fall back to using the recorder name (e.g. ProQual2014) as the tourney key
    — applicable mostly to local SGFs whose GN is just two surnames or empty.
    """
    key, rnd = categorize(name, created)
    is_fallback = key == slugify(ascii_fold(name).strip())[:32]
    if is_fallback and recorder:
        return recorder, rnd
    return key, rnd


def categorize(name: str, created: str | None) -> tuple[str, str | None]:
    year = (created or "0000")[:4]
    folded = ascii_fold(name)
    return h_handler(folded, year)


def main() -> int:
    DST_DIR.mkdir(exist_ok=True)
    d = json.loads(INDEX.read_text())

    # For entries with no `created` (local SGFs without DT tag), synthesize a
    # date from the recorder folder name (e.g. "ProQual2021" -> "2021-01-01")
    # so we can still sort and assign mtimes.
    for r in d["reviews"]:
        if not r.get("created"):
            m = re.search(r"(20\d{2})", r.get("recorder") or "")
            year = m.group(1) if m else "1970"
            r["created"] = f"{year}-01-01T00:00:00+00:00"
            r["_synthetic_date"] = True

    # Sort by created timestamp (full ISO), then by review id for stable tiebreak
    rows = sorted(d["reviews"], key=lambda r: (r["created"] or "", r["id"]))

    # Categorize
    enriched: list[dict] = []
    for r in rows:
        key, rnd = categorize_with_recorder_fallback(
            r["name"], r["created"], r.get("recorder")
        )
        enriched.append({**r, "tourney_key": key, "round": rnd})

    # Fill missing rounds by within-tournament chronological order (Czech Championship etc.)
    by_tourney: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(enriched):
        if r["round"] is None:
            by_tourney[r["tourney_key"]].append(i)
    for indices in by_tourney.values():
        # Same tournament, ordered by created — assign R1, R2, ...
        # Sort indices by created (already sorted globally so they should be in order)
        indices.sort(key=lambda i: (enriched[i]["created"], enriched[i]["id"]))
        for n, i in enumerate(indices, 1):
            enriched[i]["round"] = f"R{n}"

    # Build filenames; copy; set mtime to `created`
    used_names: set[str] = set()
    copied = 0
    for r in enriched:
        date_str = r["created"][:10]  # YYYY-MM-DD
        round_str = r["round"] or "X"
        fname = f"{date_str}_{r['tourney_key']}_{round_str}_{r['id']}.sgf"
        # Deduplicate (shouldn't happen since id is unique, but defensive)
        if fname in used_names:
            fname = f"{date_str}_{r['tourney_key']}_{round_str}_{r['id']}_dup.sgf"
        used_names.add(fname)
        src = SRC_DIR / f"{r['id']}.sgf"
        dst = DST_DIR / fname
        if not src.exists():
            print(f"  MISSING: {src}")
            continue
        shutil.copy2(src, dst)
        # Set mtime/atime to the demo's `created` timestamp
        ts = datetime.fromisoformat(r["created"].replace("Z", "+00:00")).timestamp()
        os.utime(dst, (ts, ts))
        copied += 1

    print(f"Copied {copied}/{len(enriched)} files to {DST_DIR}")
    print()
    print("=== Per-tournament counts ===")
    counts: dict[str, int] = defaultdict(int)
    for r in enriched:
        counts[r["tourney_key"]] += 1
    for k, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {n:>3}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
