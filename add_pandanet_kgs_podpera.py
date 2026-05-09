#!/usr/bin/env python3
"""Find all Podpera games in pandanet/ and kgs/, add them to podpera-sgfs/.

Podpera plays as 'Lukan' on both Pandanet and KGS. Detection rules:
  - Pandanet SGFs: filename contains 'Podpera' (already named) OR PB/PW = 'Lukan'
  - KGS SGFs:      filename or PB/PW contains 'Lukan'

Output filenames carry source + descriptive metadata so they don't collide
with the existing OGS <id>.sgf files in podpera-sgfs/.
  Pandanet PETC: pandanet__<season>__R<NN>__<TeamA>-vs-<TeamB>__<black>-vs-<white>.sgf
  KGS:          kgs__<YYYY-MM-DD>__<black>-vs-<white>.sgf

Deduplicates by full-move-hash to avoid adding two copies of the same physical game.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
PANDANET_DIR = ROOT / "pandanet"
KGS_DIR = ROOT / "kgs"
DST_DIR = ROOT / "podpera-sgfs"

MOVE_RE = re.compile(r";[BW]\[([a-z]{0,2})\]")
TAG_RE = re.compile(r"([A-Z]+)\[((?:[^\]\\]|\\.)*)\]")
LUKAN_RE = re.compile(r"\blukan\b", re.I)
PODPERA_RE = re.compile(r"podpera|podpěra", re.I)


def read_sgf(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_tags(text: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    head = text[:4000]
    for k, v in TAG_RE.findall(head):
        tags.setdefault(k, v)
    return tags


def move_hash(text: str) -> str:
    moves = MOVE_RE.findall(text)
    return hashlib.sha1("".join(moves).encode()).hexdigest()


def safe(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._\-]+", "-", s).strip("-")
    return s or "x"


def is_podpera(pb: str, pw: str) -> bool:
    blob = f"{pb}|{pw}"
    return bool(LUKAN_RE.search(blob)) or bool(PODPERA_RE.search(blob))


def existing_hashes() -> set[str]:
    """Hash of all SGFs already in podpera-sgfs/, so we skip dupes."""
    DST_DIR.mkdir(exist_ok=True)
    return {move_hash(read_sgf(p)) for p in DST_DIR.glob("*.sgf")}


def add_pandanet(seen_hashes: set[str]) -> int:
    """Walk pandanet/, copy Podpera games."""
    added = 0
    for sgf in sorted(PANDANET_DIR.rglob("*.sgf")):
        # Path: pandanet/<season>/R<NN>_<TeamA>-vs-<TeamB>/B<n>_<black>-vs-<white>__<result>.sgf
        parts = sgf.relative_to(PANDANET_DIR).parts
        if len(parts) < 3:
            continue
        season, match_folder, fname = parts[0], parts[1], parts[2]
        text = read_sgf(sgf)
        tags = parse_tags(text)
        if not is_podpera(tags.get("PB", ""), tags.get("PW", "")):
            continue
        h = move_hash(text)
        if h in seen_hashes:
            continue
        # Extract round + teams from match_folder: R03_Czechia-vs-Hungary
        m = re.match(r"^(R\d+)_(.+)$", match_folder)
        round_str = m.group(1) if m else "Rxx"
        teams = m.group(2) if m else match_folder
        # Strip the .sgf board suffix; the board+players are already in fname
        board_part = fname[: -len(".sgf")] if fname.endswith(".sgf") else fname
        new_name = f"pandanet__{safe(season)}__{round_str}__{safe(teams)}__{safe(board_part)}.sgf"
        dst = DST_DIR / new_name
        if dst.exists():
            continue
        shutil.copy2(sgf, dst)
        seen_hashes.add(h)
        added += 1
    return added


def add_kgs(seen_hashes: set[str]) -> int:
    """Walk kgs/, copy any SGF where PB or PW is 'Lukan'."""
    added = 0
    for sgf in sorted(KGS_DIR.rglob("*.sgf")):
        text = read_sgf(sgf)
        tags = parse_tags(text)
        pb = tags.get("PB", "")
        pw = tags.get("PW", "")
        if not (
            LUKAN_RE.search(pb) or LUKAN_RE.search(pw) or LUKAN_RE.search(sgf.name)
        ):
            continue
        h = move_hash(text)
        if h in seen_hashes:
            continue
        # Date from DT or filename
        dt = (tags.get("DT") or "").split(",")[0].strip()
        if not re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", dt):
            # Try filename: 2018-10-10-Lukan-SIscurge.sgf
            fm = re.match(r"^(\d{4}-\d{1,2}-\d{1,2})-", sgf.name)
            dt = fm.group(1) if fm else "unknown-date"
        # Normalize date to YYYY-MM-DD with zero-padded month/day
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", dt):
            y, mo, d = dt.split("-")
            dt = f"{y}-{int(mo):02d}-{int(d):02d}"
        new_name = f"kgs__{dt}__{safe(pb)}-vs-{safe(pw)}.sgf"
        dst = DST_DIR / new_name
        if dst.exists():
            continue
        shutil.copy2(sgf, dst)
        seen_hashes.add(h)
        added += 1
    return added


def main() -> int:
    DST_DIR.mkdir(exist_ok=True)
    print("Hashing existing podpera-sgfs/ to skip duplicates...")
    seen = existing_hashes()
    print(
        f"  existing: {len(list(DST_DIR.glob('*.sgf')))} files / {len(seen)} unique hashes"
    )

    print("\nScanning pandanet/...")
    p = add_pandanet(seen)
    print(f"  added {p} Pandanet Podpera SGFs")

    print("\nScanning kgs/...")
    k = add_kgs(seen)
    print(f"  added {k} KGS Lukan SGFs")

    print(f"\nTotal in podpera-sgfs/ now: {len(list(DST_DIR.glob('*.sgf')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
