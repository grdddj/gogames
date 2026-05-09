#!/usr/bin/env python3
"""Crawl Pandanet European Team Championship and download all SGFs.

Output structure:
    pandanet/<season>/<round-folder>/<match-folder>/<board-file>.sgf
e.g. pandanet/2025-26_A/R01_2025-10-14_Poland-vs-Czechia/B1_Podpera-vs-Surma__W+6.5.sgf

Also writes:
    reviews_pandanet.json    — metadata in the same shape as reviews_*.json
    pandanet_aliases.json    — {pandanet_handle: real_name} mapping
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
PANDANET_ROOT = "https://pandanet-igs.com"
COMMUNITY_URL = f"{PANDANET_ROOT}/communities/euroteamchamps"
OUT_DIR = ROOT / "pandanet"
ALIASES_FILE = ROOT / "pandanet_aliases.json"
REVIEWS_FILE = ROOT / "reviews_pandanet.json"
SLEEP_S = 0.4
USER_AGENT = "ogs-archiver/0.1 pandanet-etc-crawler (+local research)"
COUNTRY_FILTER = "CZE"  # only download matches where one team has this country code


def http_get(url: str, want_text: bool = True) -> tuple[int, str | bytes]:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as resp:
        data = resp.read()
        return resp.status, (data.decode("utf-8") if want_text else data)


def ascii_fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def slugify_name(s: str) -> str:
    """Filename-safe surname."""
    s = ascii_fold(s)
    s = re.sub(r"[^A-Za-z\-]", "", s)
    return s or "X"


def safe_path(s: str) -> str:
    """Filename-safe component (preserves more punctuation than slugify_name)."""
    s = ascii_fold(s)
    s = re.sub(r"[^A-Za-z0-9._\-]+", "-", s).strip("-")
    return s or "x"


def synthetic_id(url: str) -> int:
    """Stable negative int from Pandanet SGF URL (distinct from OGS ids)."""
    h = hashlib.sha1(url.encode()).hexdigest()
    return -(
        0x80000000 + int(h[:7], 16)
    )  # large-magnitude negatives, no clash with local merge ids


# --- Step 1: discover seasons --------------------------------------------
SEASON_LINK_RE = re.compile(
    r'<a [^>]*href="(?:https?://[^/]+)?/communities/euroteamchamps/(\d+)"[^>]*>\s*Season\s+(\d{4}/\d{4})',
    re.I,
)


def discover_seasons() -> list[tuple[int, str]]:
    print(f"Fetching {COMMUNITY_URL}", flush=True)
    _, html = http_get(COMMUNITY_URL)
    seasons = SEASON_LINK_RE.findall(str(html))
    out = [(int(sid), label) for sid, label in seasons]
    # The current/active season isn't listed as "Season YYYY/YYYY" but is
    # exposed via the default schedule URL. Find it and prepend.
    cur_m = re.search(
        r'href="(?:https?://[^/]+)?/communities/euroteamchamps/schedule/(\d+)"',
        str(html),
    )
    if cur_m:
        cur_id = int(cur_m.group(1))
        if not any(sid == cur_id for sid, _ in out):
            # Best-effort label: derive from highest-known season's label + 1
            if out:
                latest_year = int(out[0][1].split("/")[1])
                cur_label = f"{latest_year}/{latest_year + 1}"
            else:
                cur_label = "current"
            out.insert(0, (cur_id, cur_label))
    print(f"  {len(out)} seasons found")
    return out


# --- Step 2: discover rounds in a season ---------------------------------
# Modern (current season): schedule page lists rounds with ?tournament_id=
# Older seasons: season root lists /draw/<id>; each draw lists /rounds/<id>.
ROUND_HREF_RE = re.compile(
    r'href="(/communities/euroteamchamps/rounds/(\d+))(?:\?[^"]*)?"', re.I
)
DRAW_HREF_RE = re.compile(
    r'href="(?:https?://[^/]+)?(/communities/euroteamchamps/draw/(\d+))(?:\?[^"]*)?"',
    re.I,
)


def _safe_get_html(url: str) -> str | None:
    try:
        _, html = http_get(url)
        return str(html)
    except Exception as e:
        print(f"    WARN: {url} -> {e}", file=sys.stderr)
        return None


def discover_rounds(season_id: int) -> list[tuple[int, str]]:
    """Return [(round_id, full_url), ...]."""
    seen: dict[int, str] = {}

    # Try modern schedule path
    sched_html = _safe_get_html(
        f"{PANDANET_ROOT}/communities/euroteamchamps/schedule/{season_id}?tournament_id={season_id}"
    )
    if sched_html:
        for path, rid in ROUND_HREF_RE.findall(sched_html):
            seen[int(rid)] = urljoin(PANDANET_ROOT, f"{path}?tournament_id={season_id}")

    # Fall back: season root → draws → rounds
    if not seen:
        season_html = _safe_get_html(
            f"{PANDANET_ROOT}/communities/euroteamchamps/{season_id}"
        )
        if season_html:
            draw_ids = {did for _, did in DRAW_HREF_RE.findall(season_html)}
            for draw_id in sorted(draw_ids, key=int):
                time.sleep(SLEEP_S)
                draw_html = _safe_get_html(
                    f"{PANDANET_ROOT}/communities/euroteamchamps/draw/{draw_id}"
                )
                if not draw_html:
                    continue
                for path, rid in ROUND_HREF_RE.findall(draw_html):
                    rid_int = int(rid)
                    if rid_int not in seen:
                        seen[rid_int] = urljoin(PANDANET_ROOT, path)

    return sorted(seen.items())


# --- Step 3: parse a round page into matches + games ---------------------
TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.DOTALL | re.I)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.I)
TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL | re.I)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.I)
SGF_LINK_RE = re.compile(
    r'<a [^>]*href="(/system/sgfs/(\d+)/original/([^"]+\.sgf)(?:\?\d+)?)"[^>]*>([^<]*)</a>',
    re.I,
)
PLAYER_RE = re.compile(
    r'<a [^>]*href="/communities/euroteamchamps/teams/\d+/([A-Z]{3})[^"]*"[^>]*>([^<]+)</a>',
    re.I,
)
COLOR_RE = re.compile(r'<img alt="([BW])"', re.I)
RANK_RE = re.compile(r"(\d+\s+(?:dan|kyu)(?:\s+pro)?)", re.I)


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def extract_paren_handle(name_with_handle: str) -> tuple[str, str | None]:
    """'Lukáš Podpěra (Lukan)' -> ('Lukáš Podpěra', 'Lukan')."""
    m = re.search(r"^(.*?)\s*\(([^)]+)\)\s*$", name_with_handle.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return name_with_handle.strip(), None


def parse_round(html: str) -> list[dict]:
    """Return list of game dicts extracted from one round page."""
    games: list[dict] = []
    for table in TABLE_RE.findall(html):
        if "system/sgfs" not in table:
            continue
        # Header: team names are in <th>
        ths = [
            strip_tags(html_module.unescape(t)).strip() for t in TH_RE.findall(table)
        ]
        # Expected: [TeamA, TeamB, "Score"] (3 ths) — sometimes more if extra columns
        if len(ths) < 2:
            continue
        team_a, team_b = ths[0], ths[1]

        for row in ROW_RE.findall(table):
            tds = TD_RE.findall(row)
            if len(tds) < 3:
                continue
            sgf_match = SGF_LINK_RE.search(row)
            if not sgf_match:
                continue
            sgf_path, sgf_id, sgf_filename, result_text = sgf_match.groups()
            sgf_url = urljoin(PANDANET_ROOT, sgf_path)

            # Two player columns: tds[0] (Team A) and tds[1] (Team B)
            players: list[dict] = []
            for col_idx in (0, 1):
                cell = tds[col_idx]
                pm = PLAYER_RE.search(cell)
                cm = COLOR_RE.search(cell)
                rm = RANK_RE.search(strip_tags(cell))
                if not pm:
                    continue
                country, name_handle = pm.groups()
                full_name, handle = extract_paren_handle(
                    html_module.unescape(name_handle)
                )
                players.append(
                    {
                        "country": country,
                        "name": full_name,
                        "handle": handle,
                        "color": cm.group(1) if cm else None,  # "B" or "W"
                        "rank": rm.group(1) if rm else None,
                    }
                )
            if len(players) != 2:
                continue

            # Filter: only keep games where at least one player has the target country
            if COUNTRY_FILTER and not any(
                p.get("country") == COUNTRY_FILTER for p in players
            ):
                continue

            # Pull date from filename: ...-YYYY-MM-DD.sgf or 1_x_y.sgf
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", sgf_filename)
            game_date = dm.group(1) if dm else None

            games.append(
                {
                    "sgf_id": int(sgf_id),
                    "sgf_url": sgf_url,
                    "sgf_filename": sgf_filename,
                    "result": html_module.unescape(result_text).strip(),
                    "team_a": team_a,
                    "team_b": team_b,
                    "players": players,
                    "date": game_date,
                }
            )
    return games


# --- Step 4: orchestrate -------------------------------------------------


def season_label_to_key(label: str) -> str:
    # "2025/2026" -> "2025-26"
    a, b = label.split("/")
    return f"{a}-{b[2:]}"


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    seasons = discover_seasons()

    aliases: dict[str, str] = {}  # handle -> real_name (most-recent wins)
    all_games: list[dict] = []

    for season_id, label in seasons:
        season_key = season_label_to_key(label)
        print(f"\n=== Season {label} (id={season_id}) ===", flush=True)
        time.sleep(SLEEP_S)
        try:
            rounds = discover_rounds(season_id)
        except Exception as e:
            print(f"  WARN: discover_rounds failed: {e}", file=sys.stderr)
            continue
        print(f"  {len(rounds)} rounds", flush=True)

        for round_idx, (_round_id, round_url) in enumerate(rounds, 1):
            time.sleep(SLEEP_S)
            try:
                _, html = http_get(round_url)
            except Exception as e:
                print(f"    R{round_idx} fetch failed: {e}", file=sys.stderr)
                continue
            games = parse_round(str(html))
            if not games:
                continue
            # Group games by (team_a, team_b) — that's a "match"
            matches: dict[tuple[str, str], list[dict]] = {}
            for g in games:
                key = (g["team_a"], g["team_b"])
                matches.setdefault(key, []).append(g)

            for (team_a, team_b), match_games in matches.items():
                # Build folder: pandanet/<season>/R<NN>_<TeamA>-vs-<TeamB>/
                # (date dropped — season+round are sufficient context)
                round_str = f"R{round_idx:02d}"
                match_dir = (
                    OUT_DIR
                    / season_key
                    / f"{round_str}_{safe_path(team_a)}-vs-{safe_path(team_b)}"
                )
                match_dir.mkdir(parents=True, exist_ok=True)

                for board_idx, g in enumerate(match_games, 1):
                    # Board file: B1_<black-surname>-vs-<white-surname>__<result>.sgf
                    by_color = {p["color"]: p for p in g["players"] if p["color"]}
                    black = by_color.get("B")
                    white = by_color.get("W")
                    if not (black and white):
                        # Fall back to first/second players
                        black, white = g["players"][0], g["players"][1]
                    b_name = slugify_name(black["name"].split()[-1])
                    w_name = slugify_name(white["name"].split()[-1])
                    result_safe = safe_path(g["result"]) if g["result"] else "noresult"
                    fname = f"B{board_idx}_{b_name}-vs-{w_name}__{result_safe}.sgf"
                    out_path = match_dir / fname

                    if not out_path.exists():
                        try:
                            time.sleep(SLEEP_S)
                            _, sgf_bytes = http_get(g["sgf_url"], want_text=False)
                            assert isinstance(sgf_bytes, bytes)
                            out_path.write_bytes(sgf_bytes)
                            # Set mtime to game date
                            if g["date"]:
                                ts = datetime.fromisoformat(
                                    f"{g['date']}T12:00:00+00:00"
                                ).timestamp()
                                import os as _os

                                _os.utime(out_path, (ts, ts))
                        except Exception as e:
                            print(f"      DL fail {g['sgf_url']}: {e}", file=sys.stderr)
                            continue

                    # Aliases
                    for p in g["players"]:
                        if p["handle"] and p["name"]:
                            aliases[p["handle"]] = p["name"]

                    # Metadata
                    sid = synthetic_id(g["sgf_url"])
                    rec = {
                        "id": sid,
                        "name": f"PETC {label} R{round_idx} {team_a}-{team_b} B{board_idx}",
                        "created": f"{g['date']}T12:00:00+00:00" if g["date"] else None,
                        "updated": f"{g['date']}T12:00:00+00:00" if g["date"] else None,
                        "private": False,
                        "owner": {"id": None, "username": "pandanet-etc"},
                        "controller": {"id": None, "username": "pandanet-etc"},
                        "game": {"id": g["sgf_id"], "width": 19, "height": 19},
                        "players": {
                            "black": {
                                "id": 0,
                                "username": black["name"],
                                "ranking": None,
                                "professional": "pro"
                                in (black.get("rank") or "").lower(),
                                "country": black.get("country"),
                                "handle": black.get("handle"),
                            },
                            "white": {
                                "id": 0,
                                "username": white["name"],
                                "ranking": None,
                                "professional": "pro"
                                in (white.get("rank") or "").lower(),
                                "country": white.get("country"),
                                "handle": white.get("handle"),
                            },
                        },
                        "auth": None,
                        "review_chat_auth": None,
                        "review_voice_auth": None,
                        "recorder": "pandanet-etc",
                        "recorder_id": None,
                        "recorder_group": "pandanet",
                        "_pandanet_url": g["sgf_url"],
                        "_pandanet_sgf_id": g["sgf_id"],
                        "_pandanet_match": {
                            "team_a": team_a,
                            "team_b": team_b,
                            "round": round_idx,
                            "season": label,
                        },
                        "_local_path": str(out_path),
                        "_result": g["result"],
                    }
                    all_games.append(rec)
            print(
                f"    R{round_idx:02d}: {len(games):>3} games / {len(matches)} matches",
                flush=True,
            )

    # Write metadata
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "group": "pandanet",
        "accounts": [
            {
                "id": None,
                "username": "pandanet-etc",
                "found": True,
                "review_count": len(all_games),
            }
        ],
        "total": len(all_games),
        "reviews": all_games,
    }
    REVIEWS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    ALIASES_FILE.write_text(
        json.dumps(aliases, indent=2, ensure_ascii=False, sort_keys=True)
    )
    print(
        f"\nWrote {len(all_games)} games into {OUT_DIR}, metadata to {REVIEWS_FILE.name}"
    )
    print(f"Wrote {len(aliases)} pandanet handle->name aliases to {ALIASES_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
