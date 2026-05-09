#!/usr/bin/env python3
"""Rename pandanet__/kgs__ files in podpera-sgfs/ to date-first format.

New scheme (sortable, source after date):
  Pandanet:  YYYY-MM-DD_pandanet_<season>_<round>_<black>-vs-<white>.sgf
  KGS:       YYYY-MM-DD_kgs_league_<black>-vs-<white>.sgf

mtime is set to the game date so SmartGo's mtime-sort matches the filename sort.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent / "podpera-sgfs"

TAG_RE = re.compile(r"([A-Z]+)\[((?:[^\]\\]|\\.)*)\]")


def parse_date(text: str, fallback: str | None) -> str | None:
    head = text[:4000]
    for k, v in TAG_RE.findall(head):
        if k == "DT":
            v = v.split(",")[0].strip()
            m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", v)
            if m:
                y, mo, d = m.groups()
                return f"{y}-{int(mo):02d}-{int(d):02d}"
    if fallback:
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", fallback)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


def safe(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._\-]+", "-", s).strip("-")
    return s or "x"


def parse_pandanet_old(name: str) -> tuple[str, str, str] | None:
    """Old: pandanet__<season>__<round>__<teams>__<board_part>.sgf
    Returns (season, round, board_part) or None."""
    m = re.match(r"^pandanet__([\w\-]+)__(R\d+)__[^_]+(?:-vs-[^_]+)?__(.+)\.sgf$", name)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def parse_kgs_old(name: str) -> str | None:
    """Old: kgs__<date>__<black>-vs-<white>.sgf
    Returns 'black-vs-white' or None."""
    m = re.match(r"^kgs__\d{4}-\d{2}-\d{2}__(.+)\.sgf$", name)
    return m.group(1) if m else None


def extract_players(text: str) -> tuple[str, str]:
    head = text[:4000]
    pb = pw = ""
    for k, v in TAG_RE.findall(head):
        if k == "PB" and not pb:
            pb = v
        elif k == "PW" and not pw:
            pw = v
    return pb, pw


def opponent_first_surnames(pb: str, pw: str) -> tuple[str, str]:
    """Return (black-side-name, white-side-name) using last token as surname."""

    def last(s: str) -> str:
        s = re.sub(r"\s*\([^)]*\)\s*", "", s).strip()
        s = re.sub(r"\s+\d+[dpk]\??\s*$", "", s, flags=re.I)
        toks = s.split()
        return toks[-1] if toks else "X"

    return safe(last(pb)), safe(last(pw))


def main() -> int:
    renamed = 0
    for sgf in sorted(ROOT.glob("*.sgf")):
        if not (sgf.name.startswith("pandanet__") or sgf.name.startswith("kgs__")):
            continue

        text = sgf.read_text(errors="replace")
        date = parse_date(
            text, sgf.name.split("__", 2)[1] if "__" in sgf.name else None
        )
        if not date and sgf.name.startswith("pandanet__"):
            # Fall back to season midpoint (e.g. "pandanet__2022-23__..." -> 2023-03-01)
            sm = re.match(r"^pandanet__(\d{4})-(\d{2})__", sgf.name)
            if sm:
                start_year = int(sm.group(1))
                date = f"{start_year + 1}-03-01"
                print(f"  WARN: no DT in {sgf.name}, using season fallback {date}")
        if not date:
            print(f"  WARN: no date for {sgf.name}, skipping")
            continue

        pb, pw = extract_players(text)
        b_surname, w_surname = opponent_first_surnames(pb, pw)

        if sgf.name.startswith("pandanet__"):
            parsed = parse_pandanet_old(sgf.name)
            if not parsed:
                print(f"  WARN: cannot parse pandanet name {sgf.name}, skipping")
                continue
            season, round_str, _board_part = parsed
            new_name = (
                f"{date}_pandanet_{season}_{round_str}_{b_surname}-vs-{w_surname}.sgf"
            )
        else:  # kgs__
            new_name = f"{date}_kgs_league_{b_surname}-vs-{w_surname}.sgf"

        # Disambiguate filename collisions
        new_path = ROOT / new_name
        n = 2
        base = new_name[: -len(".sgf")]
        while new_path.exists() and new_path != sgf:
            new_path = ROOT / f"{base}_{n}.sgf"
            n += 1

        if new_path != sgf:
            sgf.rename(new_path)
        # Set mtime to the game date
        ts = (
            datetime.fromisoformat(f"{date}T12:00:00+00:00")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        os.utime(new_path, (ts, ts))
        renamed += 1

    print(f"Renamed {renamed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
