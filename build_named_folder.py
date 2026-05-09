#!/usr/bin/env python3
"""Build a renamed copy of podpera-sgfs/ with descriptive filenames.

Filename scheme:
    YYYY-MM-DD_<TourneyKey>_<Round>_<BlackSurname>-<WhiteSurname>.sgf

The SGF's GN tag is rewritten to the same string (without the .sgf extension)
so the metadata inside the file matches the filename.

Each output file's mtime is set to the demo's `created` timestamp so that any
tool sorting by file date (e.g. SmartGo) shows games in chronological order.

Usage:
    build_named_folder.py [<index.json> <out-dir>]
Defaults to podpera_games_dedup.json -> podpera-sgfs-named-dedup/
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
SRC_DIR = ROOT / "sgfs"
INDEX = Path(sys.argv[1]) if len(sys.argv) > 2 else ROOT / "podpera_games_dedup.json"
DST_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "podpera-sgfs-named-dedup"

RANK_RE = re.compile(r"\s+\d{1,2}[dpk]\??\s*$", re.I)
PARENS_RE = re.compile(r"\s*\([^)]*\)\s*$")


def ascii_fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def slugify(s: str) -> str:
    s = ascii_fold(s)
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s or "x"


def canonical_player(name: str) -> str:
    if not name:
        return ""
    n = RANK_RE.sub("", name.strip())
    n = re.sub(r"[,\.()]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = fold_lower(n)
    return " ".join(sorted(n.split()))


def fold_lower(s: str) -> str:
    return ascii_fold(s).lower()


def clean_name(name: str) -> str:
    n = name.strip()
    n = re.sub(r"\s*\([^)]*\)\s*", " ", n)  # strip embedded "(2p)"
    n = RANK_RE.sub("", n)
    return n.strip()


# --- Surname resolver -------------------------------------------------------
# Build a (canonical_name -> canonical_surname) map by scanning all known
# spellings of each player across all reviews_*.json. Heuristics in order:
#   1. Any UPPERCASE multi-letter token across spellings -> that token is surname
#      (e.g. "PODPERA Lukas" -> Podpera).
#   2. Any spelling with comma "Surname, Firstname" -> token before comma.
#   3. Else: use the last token of the most-common spelling (Western convention).


def _candidate_surnames(spellings: list[str]) -> tuple[str | None, list[str]]:
    """Return (uppercase_surname_if_found, comma_surnames). Used for resolution.

    Comma form only counts if the head (text before comma) is a single token —
    avoids matching rank-with-comma cases like "Cornel Burzo, 6d".
    """
    upper = None
    commas: list[str] = []
    for s in spellings:
        for tok in re.split(r"[\s,]+", clean_name(s)):
            if len(tok) >= 3 and tok.isupper() and tok.isalpha():
                upper = tok
        if "," in s:
            head = s.split(",")[0].strip()
            head = clean_name(head)
            # Real "Surname, Firstname" form: head is a single word (or hyphenated)
            if head and " " not in head:
                commas.append(head)
    return upper, commas


# Manual surname overrides. Keys are canonical (token-sorted, lowercased)
# player names; values are the preferred filename surname. Use this for cases
# where automatic resolution is fundamentally ambiguous (e.g. Chinese names
# where surname comes first and there's no uppercase/comma signal).
SURNAME_OVERRIDES: dict[str, str] = {
    "li yuanzhen": "Li",
    "li ting": "Li",
    "wu xi": "Wu",
    "kim dohyup": "Kim",
    "kwon hyukjoon": "Kwon",
    "yuze xing": "Xing",
    "xing yuze": "Xing",
    "bao yifan": "Bao",
    "cho daehee": "Cho",
    "daehee cho": "Cho",
    "park junghun": "Park",
    "junghun park": "Park",
    "chi-min oh": "Oh",
    "min oh chi-": "Oh",  # safety for sorted-token edge cases
    "oh chi-min": "Oh",
    "fu tianyi": "Fu",
    "tianyi fu": "Fu",
}


def build_surname_map() -> dict[str, str]:
    """Walk all reviews_*.json, return {canonical_player_name: PrettySurname}."""
    spellings_by_canon: dict[str, Counter] = defaultdict(Counter)
    for rfile in sorted(ROOT.glob("reviews_*.json")):
        d = json.loads(rfile.read_text())
        for r in d["reviews"]:
            p = r.get("players") or {}
            for color in ("black", "white"):
                u = (p.get(color) or {}).get("username") or ""
                if not u:
                    continue
                spellings_by_canon[canonical_player(u)][u] += 1

    surname_of: dict[str, str] = {}
    for canon, counter in spellings_by_canon.items():
        if canon in SURNAME_OVERRIDES:
            surname_of[canon] = SURNAME_OVERRIDES[canon]
            continue
        spellings = list(counter.keys())
        upper, commas = _candidate_surnames(spellings)
        if upper:
            surname_of[canon] = upper.title()
        elif commas:
            surname_of[canon] = Counter(commas).most_common(1)[0][0].title()
        else:
            # Western convention: last token of most-common spelling
            best = counter.most_common(1)[0][0]
            tokens = clean_name(best).split()
            surname_of[canon] = (tokens[-1] if tokens else "Unknown").title()
    return surname_of


def slugify_surname(name: str) -> str:
    """Filename-safe surname (drops accents, hyphens preserved)."""
    s = ascii_fold(name)
    s = re.sub(r"[^A-Za-z\-]", "", s)
    return s or "X"


# --- Round / tournament categorization (unchanged from prior version) -------


def find_round(name: str) -> str | None:
    n = name.strip()
    m = re.search(r"\bround\s+(\d+)\.\d+", n, re.I)
    if m:
        return f"R{m.group(1)}"
    m = re.search(r"places?\s+(\d+)\s*-\s*(\d+)", n, re.I)
    if m:
        return f"P{m.group(1)}-{m.group(2)}"
    if re.search(r"\b1/4\b|quarter[- ]?final", n, re.I):
        return "QF"
    if re.search(r"semi[- ]?final", n, re.I):
        return "SF"
    if re.search(r"\b3/4\b", n, re.I):
        return "3-4"
    if re.search(r"\bfor\s+5th\s+place\b", n, re.I):
        return "5th"
    m = re.search(r"\bmatch\s*(\d+)", n, re.I)
    if m:
        return f"M{m.group(1)}"
    m = re.search(r"\bfinal[s]?\s*(\d+)", n, re.I)
    if m:
        return f"F{m.group(1)}"
    m = re.search(r"\bgame\s*(\d+)", n, re.I)
    if m:
        return f"G{m.group(1)}"
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
    if re.search(r"\bfinal[s]?\b", n, re.I):
        return "Final"
    return None


def h_handler(name: str, created_year: str) -> tuple[str, str | None]:
    n = name.strip()
    if re.search(r"\b(?:czech\s+)?(?:go\s*)?baron\b", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"CZ-Baron-{m.group(1) if m else created_year}", find_round(n)
    m = re.match(r"^Czech\s+Championship\s+(\d{4})\s*$", n, re.I)
    if m:
        return f"CZ-Champ-{m.group(1)}", None
    if (
        re.search(r"\bCEGO\b", n, re.I)
        or re.search(r"european\s+grand\s+slam", n, re.I)
        or re.search(r"^Grand\s+Slam\s+(\d{4})", n, re.I)
    ):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"CEGO-GS-{m.group(1) if m else created_year}", find_round(n)
    if re.search(
        r"ProQuali|\bEPQ\b|Pro\s+Quali|European\s+Pro\s+Qualification|EGF\s+PRO\s+Qualif",
        n,
        re.I,
    ):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"EGF-ProQ-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"Croatian\s+Open", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"CroatianOpen-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"Velika\s+Gorica", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"VelikaGorica-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"Lanke\s+Cup", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"LankeVienna-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"Vienna\s+Open", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"ViennaOpen-{m.group(1) if m else created_year}", find_round(n)
    m = re.match(r"^\s*(\d+)\w*\s+Prague\s+(?:Go\s+)?Tour", n, re.I)
    if m:
        return f"Prague-{m.group(1)}", find_round(n)
    m = re.match(r"^\s*(\d+)\w*\s+Prague\s+Tournament", n, re.I)
    if m:
        return f"Prague-{m.group(1)}", find_round(n)
    if re.search(
        r"\bEGP\b\s+(?:Finale|final)|EGF\s+Grand\s+Prix|Grand\s+Prix\s+(?:Finale|Finals)",
        n,
        re.I,
    ):
        m = re.search(r"\b(20\d{2})\b", n)
        year = m.group(1) if m else created_year
        return f"EGPF-Final-{year}", find_round(n)
    if re.search(r"\bEGPF\d*\b|European\s+Grand\s+Prix", n, re.I):
        m = re.search(r"(?:EGPF|\b)(20\d{2})\b", n)
        return f"EGPF-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"\bEGC\b", n, re.I) or re.search(r"\bweekend\b", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        year = m.group(1) if m else created_year
        flavor = "Open" if re.search(r"\bopen\b", n, re.I) else "Weekend"
        return f"EGC-{flavor}-{year}", find_round(n)
    if re.search(r"\bPETC\b", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"PETC-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"european\s+championship", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"EuroCh-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"EllieCup", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"EllieCup-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"\bBrno\b", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"Brno-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"Blansko", n, re.I):
        m = re.match(r"^\s*([IVX]+)\.\s*Blansko", n, re.I)
        edition = m.group(1) if m else created_year
        return f"Blansko-{edition}", find_round(n)
    if re.search(r"Sokolov", n, re.I):
        m = re.match(r"^\s*(\d+)\w*\s+Sokolov", n, re.I)
        edition = m.group(1) if m else created_year
        return f"Sokolov-{edition}", find_round(n)
    if re.search(r"Olomouc", n, re.I):
        return f"Olomouc-{created_year}", find_round(n)
    if re.search(r"Hungarian\s+Open", n, re.I):
        m = re.search(r"\b(20\d{2})\b", n)
        return f"HUN-Open-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"liga\s+druzstev", ascii_fold(n), re.I):
        m = re.search(r"(\d{4})", n)
        return f"CZ-Liga-{m.group(1) if m else created_year}", find_round(n)
    if re.search(r"Play\s+with\s+Brain", n, re.I):
        return f"PlayWithBrain-{created_year}", find_round(n)
    if re.search(r"IZIS2OGS", n, re.I):
        m = re.search(r"transcript\s+of\s+game\s+(\d+)", n, re.I)
        return f"IZIS-{created_year}", f"g{m.group(1)}" if m else None
    return slugify(n)[:32], None


def categorize(name: str, created: str | None) -> tuple[str, str | None]:
    year = (created or "0000")[:4]
    return h_handler(ascii_fold(name), year)


def categorize_with_recorder_fallback(
    name: str, created: str | None, recorder: str | None
) -> tuple[str, str | None]:
    key, rnd = categorize(name, created)
    is_fallback = key == slugify(ascii_fold(name).strip())[:32]
    if is_fallback and recorder:
        return recorder, rnd
    return key, rnd


# --- SGF GN editing ---------------------------------------------------------

GN_RE = re.compile(r"GN\[((?:[^\]\\]|\\.)*)\]")


def rewrite_gn(text: str, new_gn: str) -> str:
    if GN_RE.search(text):
        return GN_RE.sub(f"GN[{new_gn}]", text, count=1)
    # Insert GN tag right after the first ;FF[..] root node
    return re.sub(r"(\(\s*;)", r"\1GN[" + new_gn + "]", text, count=1)


# --- Main -------------------------------------------------------------------


def main() -> int:
    DST_DIR.mkdir(exist_ok=True)
    d = json.loads(INDEX.read_text())

    # Build surname map across the whole corpus (gives best resolution)
    surname_of = build_surname_map()

    # Synthesize created date for entries without one
    for r in d["reviews"]:
        if not r.get("created"):
            m = re.search(r"(20\d{2})", r.get("recorder") or "")
            year = m.group(1) if m else "1970"
            r["created"] = f"{year}-01-01T00:00:00+00:00"

    rows = sorted(d["reviews"], key=lambda r: (r["created"] or "", r["id"]))

    enriched: list[dict] = []
    for r in rows:
        key, rnd = categorize_with_recorder_fallback(r["name"], r["created"], r.get("recorder"))
        enriched.append({**r, "tourney_key": key, "round": rnd})

    # Fill missing rounds via within-tournament chronological order
    by_tourney: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(enriched):
        if r["round"] is None:
            by_tourney[r["tourney_key"]].append(i)
    for indices in by_tourney.values():
        indices.sort(key=lambda i: (enriched[i]["created"], enriched[i]["id"]))
        for n, i in enumerate(indices, 1):
            enriched[i]["round"] = f"R{n}"

    # Build filenames + GN
    used_names: set[str] = set()
    copied = 0
    for r in enriched:
        date_str = r["created"][:10]
        round_str = r["round"] or "X"
        # podpera_games*.json uses flat 'black'/'white' fields (extracted by the
        # search snippet); reviews_*.json uses nested players.black.username.
        # Support both.
        p = r.get("players") or {}
        pb_user = r.get("black") or (p.get("black") or {}).get("username") or ""
        pw_user = r.get("white") or (p.get("white") or {}).get("username") or ""
        b_surname = slugify_surname(surname_of.get(canonical_player(pb_user), pb_user))
        w_surname = slugify_surname(surname_of.get(canonical_player(pw_user), pw_user))
        base_name = f"{date_str}_{r['tourney_key']}_{round_str}_{b_surname}-{w_surname}"
        fname = f"{base_name}.sgf"
        # Disambiguate collisions
        n = 2
        while fname in used_names:
            fname = f"{base_name}_{n}.sgf"
            n += 1
        used_names.add(fname)

        src = SRC_DIR / f"{r['id']}.sgf"
        dst = DST_DIR / fname
        if not src.exists():
            print(f"  MISSING: {src}")
            continue

        # Read, rewrite GN, write to dst
        text = src.read_text(errors="replace")
        new_gn = base_name  # GN matches filename minus the .sgf extension
        text = rewrite_gn(text, new_gn)
        dst.write_text(text)

        # Set mtime to demo's created timestamp
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
