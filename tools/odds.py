#!/usr/bin/env python3
"""The Odds API pricing helper for Parlay Locks (paper only).

Devig each book's own two-way price at the exact same line (multiplicative),
then take the median fair probability across books. Requires 2+ books.
Unders are shaded -3 percentage points (P1). College player props are refused.

Credit model (The Odds API free tier):
  - GET /v4/sports and GET /v4/sports/{sport}/events are free.
  - GET /v4/sports/{sport}/events/{id}/odds costs (markets returned) x (regions).
    We always use regions=us (1 region), so 1 credit per market per event.
  - A daily budget (default 45) is tracked in a local file outside the repo
    and a call is refused if it would go past the budget.

Env:
  ODDS_API_KEY          required for live pulls
  PL_ODDS_DAILY_BUDGET  default 45
  PL_ODDS_BUDGET_FILE   default /home/box/parlay-locks/var/odds-budget.json
  PL_ODDS_CACHE_DIR     default /home/box/parlay-locks/var/odds-cache

CLI (also exposed as `pl price` / `pl lock` / `pl budget`):
  odds.py events --sport NBA
  odds.py price --sport NBA --event "GSW @ MEM" --market Assists [--player NAME] [--line 1.5] [--pick over]
  odds.py price --sport NFL --event-id ID --markets receptions,rush_attempts
  odds.py lock data/slips/RUN.json
  odds.py budget
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
API_BASE = "https://api.the-odds-api.com/v4"
UNDER_SHADE = 0.03
MIN_BOOKS = 2
DEFAULT_BUDGET = 45
DEFAULT_BUDGET_FILE = "/home/box/parlay-locks/var/odds-budget.json"
DEFAULT_CACHE_DIR = "/home/box/parlay-locks/var/odds-cache"
CACHE_TTL_S = 20 * 60
REPO = os.environ.get("PL_REPO", "/home/box/parlay-locks/repo")

# Sport aliases -> Odds API sport key. College keys are listed so we can refuse them.
SPORT_KEYS = {
    "nba": "basketball_nba",
    "basketball_nba": "basketball_nba",
    "nhl": "icehockey_nhl",
    "icehockey_nhl": "icehockey_nhl",
    "nfl": "americanfootball_nfl",
    "americanfootball_nfl": "americanfootball_nfl",
    "mlb": "baseball_mlb",
    "baseball_mlb": "baseball_mlb",
    "wnba": "basketball_wnba",
    "basketball_wnba": "basketball_wnba",
}
COLLEGE_SPORTS = {
    "ncaab", "cbb", "basketball_ncaab", "ncaaf", "cfb", "americanfootball_ncaaf",
    "ncaaf_fcs", "americanfootball_ncaaf_fcs", "wncaab", "basketball_wncaab",
}

# Human market names / shorthand -> Odds API market key
MARKET_ALIASES = {
    # NBA / shared
    "points": "player_points", "pts": "player_points", "player_points": "player_points",
    "rebounds": "player_rebounds", "reb": "player_rebounds", "rebs": "player_rebounds",
    "player_rebounds": "player_rebounds",
    "assists": "player_assists", "ast": "player_assists", "asts": "player_assists",
    "player_assists": "player_assists",
    "threes": "player_threes", "3pm": "player_threes", "3s": "player_threes",
    "three pointers": "player_threes", "three-pointers": "player_threes",
    "3-pointers": "player_threes", "player_threes": "player_threes",
    "blocks": "player_blocks", "blk": "player_blocks", "player_blocks": "player_blocks",
    "steals": "player_steals", "stl": "player_steals", "player_steals": "player_steals",
    "turnovers": "player_turnovers", "to": "player_turnovers", "player_turnovers": "player_turnovers",
    "pra": "player_points_rebounds_assists", "points + rebounds + assists": "player_points_rebounds_assists",
    "player_points_rebounds_assists": "player_points_rebounds_assists",
    "pr": "player_points_rebounds", "points + rebounds": "player_points_rebounds",
    "player_points_rebounds": "player_points_rebounds",
    "pa": "player_points_assists", "points + assists": "player_points_assists",
    "player_points_assists": "player_points_assists",
    "ra": "player_rebounds_assists", "rebounds + assists": "player_rebounds_assists",
    "player_rebounds_assists": "player_rebounds_assists",
    "blocks + steals": "player_blocks_steals", "player_blocks_steals": "player_blocks_steals",
    "fg": "player_field_goals", "field goals": "player_field_goals",
    "player_field_goals": "player_field_goals",
    "ftm": "player_frees_made", "free throws made": "player_frees_made",
    "player_frees_made": "player_frees_made",
    # NHL
    "sog": "player_shots_on_goal", "shots": "player_shots_on_goal",
    "shots on goal": "player_shots_on_goal", "player_shots_on_goal": "player_shots_on_goal",
    "blocked shots": "player_blocked_shots", "blocks (nhl)": "player_blocked_shots",
    "player_blocked_shots": "player_blocked_shots",
    "goals": "player_goals", "player_goals": "player_goals",
    "saves": "player_total_saves", "total saves": "player_total_saves",
    "player_total_saves": "player_total_saves",
    "ppp": "player_power_play_points", "power play points": "player_power_play_points",
    "player_power_play_points": "player_power_play_points",
    "hockey points": "player_points",
    # NFL
    "receptions": "player_receptions", "rec": "player_receptions",
    "player_receptions": "player_receptions",
    "receiving yards": "player_reception_yds", "rec yds": "player_reception_yds",
    "rec yards": "player_reception_yds", "receiving yds": "player_reception_yds",
    "player_reception_yds": "player_reception_yds",
    "rush attempts": "player_rush_attempts", "carries": "player_rush_attempts",
    "rush att": "player_rush_attempts", "player_rush_attempts": "player_rush_attempts",
    "rush yards": "player_rush_yds", "rush yds": "player_rush_yds",
    "player_rush_yds": "player_rush_yds",
    "pass yards": "player_pass_yds", "pass yds": "player_pass_yds",
    "passing yards": "player_pass_yds", "player_pass_yds": "player_pass_yds",
    "pass attempts": "player_pass_attempts", "player_pass_attempts": "player_pass_attempts",
    "pass completions": "player_pass_completions", "completions": "player_pass_completions",
    "player_pass_completions": "player_pass_completions",
    "pass tds": "player_pass_tds", "passing tds": "player_pass_tds",
    "player_pass_tds": "player_pass_tds",
    "interceptions": "player_pass_interceptions", "ints": "player_pass_interceptions",
    "player_pass_interceptions": "player_pass_interceptions",
    "rush + rec yards": "player_rush_reception_yds", "player_rush_reception_yds": "player_rush_reception_yds",
    "pass + rush yards": "player_pass_rush_yds", "player_pass_rush_yds": "player_pass_rush_yds",
    "longest reception": "player_reception_longest", "player_reception_longest": "player_reception_longest",
    "longest rush": "player_rush_longest", "player_rush_longest": "player_rush_longest",
    "tackles + assists": "player_tackles_assists", "player_tackles_assists": "player_tackles_assists",
    "solo tackles": "player_solo_tackles", "player_solo_tackles": "player_solo_tackles",
    "sacks": "player_sacks", "player_sacks": "player_sacks",
    "kicking points": "player_kicking_points", "player_kicking_points": "player_kicking_points",
    "field goals": "player_field_goals",  # NBA FG already mapped; NFL FG uses player_field_goals too? NFL is player_field_goals in docs
}

# Team aliases (abbrev / city / nickname) -> tokens used for event matching.
# Values are substrings we expect in Odds API team names (lowercase).
TEAM_ALIASES = {
    # NBA
    "atl": "hawks", "hawks": "hawks", "atlanta": "hawks",
    "bos": "celtics", "celtics": "celtics", "boston": "celtics",
    "bkn": "nets", "brooklyn": "nets", "nets": "nets",
    "cha": "hornets", "hornets": "hornets", "charlotte": "hornets",
    "chi": "bulls", "bulls": "bulls", "chicago": "bulls",
    "cle": "cavaliers", "cavs": "cavaliers", "cavaliers": "cavaliers", "cleveland": "cavaliers",
    "dal": "mavericks", "mavs": "mavericks", "mavericks": "mavericks", "dallas": "mavericks",
    "den": "nuggets", "nuggets": "nuggets", "denver": "nuggets",
    "det": "pistons", "pistons": "pistons", "detroit": "pistons",
    "gsw": "warriors", "gs": "warriors", "warriors": "warriors", "golden state": "warriors",
    "hou": "rockets", "rockets": "rockets", "houston": "rockets",
    "ind": "pacers", "pacers": "pacers", "indiana": "pacers",
    "lac": "clippers", "clippers": "clippers",
    "lal": "lakers", "lakers": "lakers",
    "mem": "grizzlies", "grizzlies": "grizzlies", "memphis": "grizzlies",
    "mia": "heat", "heat": "heat", "miami": "heat",
    "mil": "bucks", "bucks": "bucks", "milwaukee": "bucks",
    "min": "timberwolves", "wolves": "timberwolves", "timberwolves": "timberwolves", "minnesota": "timberwolves",
    "nop": "pelicans", "no": "pelicans", "pelicans": "pelicans", "new orleans": "pelicans",
    "nyk": "knicks", "ny": "knicks", "knicks": "knicks", "new york knicks": "knicks",
    "okc": "thunder", "thunder": "thunder",
    "orl": "magic", "magic": "magic", "orlando": "magic",
    "phi": "76ers", "sixers": "76ers", "76ers": "76ers", "philadelphia": "76ers",
    "phx": "suns", "suns": "suns", "phoenix": "suns",
    "por": "blazers", "blazers": "blazers", "trail blazers": "blazers", "portland": "blazers",
    "sac": "kings", "kings": "kings", "sacramento": "kings",
    "sas": "spurs", "sa": "spurs", "spurs": "spurs", "san antonio": "spurs",
    "tor": "raptors", "raptors": "raptors", "toronto": "raptors",
    "uta": "jazz", "utah": "jazz", "jazz": "jazz",
    "was": "wizards", "wsh": "wizards", "wizards": "wizards", "washington wizards": "wizards",
    # NFL
    "ari": "cardinals", "cardinals": "cardinals", "arizona": "cardinals",
    "atl falcons": "falcons", "falcons": "falcons",
    "bal": "ravens", "ravens": "ravens", "baltimore": "ravens",
    "buf": "bills", "bills": "bills", "buffalo": "bills",
    "car": "panthers", "panthers": "panthers",
    "chi bears": "bears", "bears": "bears",
    "cin": "bengals", "bengals": "bengals", "cincinnati": "bengals",
    "cle browns": "browns", "browns": "browns",
    "dal cowboys": "cowboys", "cowboys": "cowboys",
    "den broncos": "broncos", "broncos": "broncos",
    "det lions": "lions", "lions": "lions",
    "gb": "packers", "packers": "packers", "green bay": "packers",
    "hou texans": "texans", "texans": "texans",
    "ind colts": "colts", "colts": "colts", "indianapolis": "colts",
    "jax": "jaguars", "jac": "jaguars", "jaguars": "jaguars", "jacksonville": "jaguars",
    "kc": "chiefs", "chiefs": "chiefs", "kansas city": "chiefs",
    "lv": "raiders", "raiders": "raiders", "las vegas": "raiders",
    "lac chargers": "chargers", "chargers": "chargers",
    "lar": "rams", "rams": "rams",
    "mia dolphins": "dolphins", "dolphins": "dolphins",
    "min vikings": "vikings", "vikings": "vikings",
    "ne": "patriots", "pats": "patriots", "patriots": "patriots", "new england": "patriots",
    "no saints": "saints", "saints": "saints",
    "nyg": "giants", "giants": "giants",
    "nyj": "jets", "jets": "jets",
    "phi eagles": "eagles", "eagles": "eagles",
    "pit": "steelers", "steelers": "steelers", "pittsburgh": "steelers",
    "sea": "seahawks", "seahawks": "seahawks", "seattle": "seahawks",
    "sf": "49ers", "49ers": "49ers", "niners": "49ers", "san francisco": "49ers",
    "tb": "buccaneers", "bucs": "buccaneers", "buccaneers": "buccaneers", "tampa bay": "buccaneers",
    "ten": "titans", "titans": "titans", "tennessee": "titans",
    "was commanders": "commanders", "wsh commanders": "commanders", "commanders": "commanders",
    # NHL (common abbrevs)
    "ana": "ducks", "ducks": "ducks", "anaheim": "ducks",
    "ari coyotes": "coyotes", "utah hockey": "hockey club", "utah hc": "hockey club",
    "bos bruins": "bruins", "bruins": "bruins",
    "buf sabres": "sabres", "sabres": "sabres",
    "cgy": "flames", "flames": "flames", "calgary": "flames",
    "car hurricanes": "hurricanes", "canes": "hurricanes", "hurricanes": "hurricanes",
    "chi blackhawks": "blackhawks", "blackhawks": "blackhawks",
    "col": "avalanche", "avs": "avalanche", "avalanche": "avalanche", "colorado": "avalanche",
    "cbj": "blue jackets", "blue jackets": "blue jackets", "columbus": "blue jackets",
    "dal stars": "stars", "stars": "stars",
    "det red wings": "red wings", "red wings": "red wings",
    "edm": "oilers", "oilers": "oilers", "edmonton": "oilers",
    "fla": "panthers", "florida": "panthers",
    "lak": "kings", "la kings": "kings",
    "min wild": "wild", "wild": "wild",
    "mtl": "canadiens", "montreal": "canadiens", "montréal": "canadiens", "canadiens": "canadiens", "habs": "canadiens",
    "nsh": "predators", "preds": "predators", "predators": "predators", "nashville": "predators",
    "njd": "devils", "nj": "devils", "devils": "devils", "new jersey": "devils",
    "nyi": "islanders", "islanders": "islanders",
    "nyr": "rangers", "rangers": "rangers",
    "ott": "senators", "sens": "senators", "senators": "senators", "ottawa": "senators",
    "phi flyers": "flyers", "flyers": "flyers",
    "pit penguins": "penguins", "pens": "penguins", "penguins": "penguins",
    "sea kraken": "kraken", "kraken": "kraken",
    "sjs": "sharks", "sj": "sharks", "sharks": "sharks", "san jose": "sharks",
    "stl": "blues", "blues": "blues", "st louis": "blues", "st. louis": "blues",
    "tbl": "lightning", "tb lightning": "lightning", "lightning": "lightning", "tampa": "lightning",
    "tor": "maple leafs", "leafs": "maple leafs", "maple leafs": "maple leafs",
    "van": "canucks", "canucks": "canucks", "vancouver": "canucks",
    "vgk": "golden knights", "vegas": "golden knights", "golden knights": "golden knights", "knights": "golden knights",
    "wpg": "jets", "jets nhl": "jets", "winnipeg": "jets",
    "wsh capitals": "capitals", "caps": "capitals", "capitals": "capitals", "washington capitals": "capitals",
}

# Disambiguate shared city/abbrev codes so matching can hit either sport's team name.
TEAM_ALIASES.update({
    "hou": ["houston", "rockets", "texans"],
    "ind": ["indiana", "indianapolis", "pacers", "colts"],
    "chi": ["chicago", "bulls", "bears", "blackhawks"],
    "dal": ["dallas", "mavericks", "cowboys", "stars"],
    "den": ["denver", "nuggets", "broncos"],
    "det": ["detroit", "pistons", "lions", "red wings"],
    "min": ["minnesota", "timberwolves", "vikings", "wild"],
    "nyi": ["islanders"],
    "nyj": ["jets"],
    "nyk": ["knicks"],
    "nyr": ["rangers"],
    "ny": ["new york"],
    "lac": ["clippers", "chargers"],
    "lal": ["lakers"],
    "lar": ["rams"],
    "la": ["los angeles"],
    "mia": ["miami", "heat", "dolphins"],
    "phi": ["philadelphia", "76ers", "eagles", "flyers"],
    "pit": ["pittsburgh", "steelers", "penguins"],
    "sea": ["seattle", "seahawks", "kraken"],
    "sf": ["san francisco", "49ers", "niners"],
    "tb": ["tampa", "buccaneers", "lightning"],
    "was": ["washington", "wizards", "commanders", "capitals"],
    "wsh": ["washington", "wizards", "commanders", "capitals"],
    "ari": ["arizona", "cardinals", "coyotes"],
    "atl": ["atlanta", "hawks", "falcons"],
    "bal": ["baltimore", "ravens"],
    "bos": ["boston", "celtics", "bruins"],
    "buf": ["buffalo", "bills", "sabres"],
    "car": ["carolina", "panthers", "hurricanes"],
    "cle": ["cleveland", "cavaliers", "browns"],
    "col": ["colorado", "avalanche"],
    "gb": ["green bay", "packers"],
    "jax": ["jacksonville", "jaguars"],
    "kc": ["kansas city", "chiefs"],
    "lv": ["las vegas", "raiders", "golden knights"],
    "ne": ["new england", "patriots"],
    "no": ["new orleans", "pelicans", "saints"],
    "nop": ["pelicans", "new orleans"],
    "ott": ["ottawa", "senators"],
    "tor": ["toronto", "raptors", "maple leafs", "leafs"],
    "uta": ["utah", "jazz"],
    "van": ["vancouver", "canucks"],
    "wpg": ["winnipeg", "jets"],
    "nj": ["new jersey", "devils"],
    "njd": ["devils", "new jersey"],
})



class OddsError(Exception):
    """User-facing error (bad input, budget, API)."""


def now_et() -> datetime:
    return datetime.now(ET)


def now_et_iso() -> str:
    return now_et().isoformat(timespec="seconds")


def implied_american(odds) -> float:
    a = float(odds)
    if a >= 0:
        return 100.0 / (a + 100.0)
    return abs(a) / (abs(a) + 100.0)


def devig_multiplicative(over_odds, under_odds) -> tuple[float, float]:
    po, pu = implied_american(over_odds), implied_american(under_odds)
    s = po + pu
    if s <= 0:
        raise OddsError("invalid implied probabilities")
    return po / s, pu / s


def fmt_american(odds) -> str:
    a = int(odds)
    return f"+{a}" if a > 0 else str(a)


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("é", "e").replace("è", "e").replace("ö", "o")
    s = re.sub(r"[.']", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _player_key(name: str) -> str:
    s = _norm(name)
    s = re.sub(r"\b(jr|sr|iii|ii|iv|v)\b", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def players_match(a: str, b: str) -> bool:
    ka, kb = _player_key(a), _player_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # unique-enough last-name match when one side is a last name only
    pa, pb = ka.split(), kb.split()
    if pa[-1] == pb[-1] and (len(pa) == 1 or len(pb) == 1 or pa[0][0] == pb[0][0]):
        return True
    return False


def resolve_sport(sport: str) -> str:
    if not sport:
        raise OddsError("sport is required (NBA, NHL, NFL, …)")
    raw = sport.strip().lower()
    if raw in COLLEGE_SPORTS or raw.replace(" ", "_") in COLLEGE_SPORTS:
        raise OddsError("college player props are ineligible in Indiana and are not priced")
    key = SPORT_KEYS.get(raw) or SPORT_KEYS.get(raw.replace(" ", "_"))
    if not key:
        raise OddsError(f"unknown or unsupported sport {sport!r} (use NBA, NHL, or NFL)")
    return key


def resolve_market(market: str) -> str:
    if not market:
        raise OddsError("market is required")
    raw = _norm(market)
    if raw.startswith("player_") or raw.startswith("batter_") or raw.startswith("pitcher_"):
        return raw.replace(" ", "_")
    if raw in MARKET_ALIASES:
        return MARKET_ALIASES[raw]
    # bare words like "Shots On Goal"
    return MARKET_ALIASES.get(raw, f"player_{raw.replace(' ', '_').replace('+', '').replace('__', '_')}")


def resolve_markets(markets) -> list[str]:
    if markets is None:
        return []
    if isinstance(markets, str):
        parts = [p for p in re.split(r"[,,]+", markets) if p.strip()]
    else:
        parts = list(markets)
    out, seen = [], set()
    for p in parts:
        k = resolve_market(p)
        if k not in seen:
            out.append(k)
            seen.add(k)
    return out


def _team_token(name: str) -> str:
    n = _norm(name)
    if n in TEAM_ALIASES:
        return TEAM_ALIASES[n]
    # last word often is the nickname
    return n


def parse_event_sides(event: str) -> tuple[str, str] | None:
    if not event:
        return None
    s = event.strip()
    parts = re.split(r"\s+@\s+|\s+vs\.?\s+|\s+at\s+|\s+v\s+", s, maxsplit=1, flags=re.I)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None


def _team_needles(name: str) -> list[str]:
    """Expand a team query into substrings that may appear in an Odds API team name."""
    n = _norm(name)
    if not n:
        return []
    out = [n]
    if n in TEAM_ALIASES:
        al = TEAM_ALIASES[n]
        out.append(al if isinstance(al, str) else "")
        if isinstance(al, (list, tuple)):
            out.extend(al)
    # also consider last word (nickname) and first word (city)
    out.extend(n.split())
    # unique, drop empties and very short tokens like "ny" handled via aliases only if length <= 2 and not aliased
    seen, needles = set(), []
    for tok in out:
        tok = _norm(str(tok))
        if not tok or tok in seen:
            continue
        seen.add(tok)
        needles.append(tok)
    return needles


def team_matches(query: str, team_name: str) -> bool:
    t = _norm(team_name)
    if not query or not t:
        return False
    for tok in _team_needles(query):
        if len(tok) <= 2:
            continue  # abbreviations must go through aliases that expand to longer names
        if tok in t or tok in t.split():
            return True
    # exact alias nickname in team name
    n = _norm(query)
    if n in TEAM_ALIASES:
        al = TEAM_ALIASES[n]
        cands = [al] if isinstance(al, str) else list(al)
        for c in cands:
            if _norm(c) and _norm(c) in t:
                return True
    return False


def event_matches(ev: dict, query: str) -> bool:
    """True if query (id, 'AWAY @ HOME', or a team name) matches this events object."""
    if not query:
        return False
    q = query.strip()
    if ev.get("id") and q.lower() == ev["id"].lower():
        return True
    away, home = ev.get("away_team") or "", ev.get("home_team") or ""
    sides = parse_event_sides(q)
    if sides:
        a_q, h_q = sides
        if team_matches(a_q, away) and team_matches(h_q, home):
            return True
        if team_matches(a_q, home) and team_matches(h_q, away):
            return True
        return False
    return team_matches(q, away) or team_matches(q, home) or _norm(q) in _norm(f"{away} {home}")


# --------------- budget / cache / HTTP ---------------

def budget_path() -> Path:
    return Path(os.environ.get("PL_ODDS_BUDGET_FILE", DEFAULT_BUDGET_FILE))


def cache_dir() -> Path:
    return Path(os.environ.get("PL_ODDS_CACHE_DIR", DEFAULT_CACHE_DIR))


def daily_budget_limit() -> int:
    return int(os.environ.get("PL_ODDS_DAILY_BUDGET", DEFAULT_BUDGET))


def _load_budget() -> dict:
    p = budget_path()
    today = now_et().date().isoformat()
    blank = {"day": today, "used_today": 0, "daily_budget": daily_budget_limit(), "remaining_month": None, "used_month": None, "calls": []}
    if not p.exists():
        return blank
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return blank
    if d.get("day") != today:
        d = {**blank, "remaining_month": d.get("remaining_month"), "used_month": d.get("used_month")}
    d["daily_budget"] = daily_budget_limit()
    return d


def _save_budget(d: dict) -> None:
    p = budget_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def budget_status() -> dict:
    d = _load_budget()
    limit = d.get("daily_budget") or daily_budget_limit()
    used = int(d.get("used_today") or 0)
    return {
        "day": d.get("day"),
        "used_today": used,
        "daily_budget": limit,
        "remaining_today": max(0, limit - used),
        "remaining_month": d.get("remaining_month"),
        "used_month": d.get("used_month"),
        "calls_today": len(d.get("calls") or []),
    }


def _record_call(cost: int, remaining: int | None, used_month: int | None, meta: dict) -> None:
    d = _load_budget()
    d["used_today"] = int(d.get("used_today") or 0) + int(cost)
    if remaining is not None:
        d["remaining_month"] = remaining
    if used_month is not None:
        d["used_month"] = used_month
    calls = d.setdefault("calls", [])
    rec = {"ts": now_et_iso(), "cost": int(cost), **meta}
    if remaining is not None:
        rec["remaining_month"] = remaining
    calls.append(rec)
    _save_budget(d)


def assert_budget(estimated_cost: int) -> None:
    st = budget_status()
    if estimated_cost <= 0:
        return
    if st["used_today"] + estimated_cost > st["daily_budget"]:
        raise OddsError(
            f"Odds API daily budget would be exceeded: used {st['used_today']}+{estimated_cost} "
            f"> {st['daily_budget']} (remaining today {st['remaining_today']}). "
            f"Raise PL_ODDS_DAILY_BUDGET or wait until tomorrow."
        )


def _api_key() -> str:
    k = os.environ.get("ODDS_API_KEY") or ""
    if not k:
        raise OddsError("ODDS_API_KEY is not set")
    return k


def _headers_usage(headers) -> tuple[int | None, int | None, int]:
    def g(*names):
        for n in names:
            v = headers.get(n) or headers.get(n.lower()) or headers.get(n.title())
            if v is not None and str(v).strip() != "":
                try:
                    return int(v)
                except ValueError:
                    return None
        return None
    # urllib headers are case-insensitive
    rem = g("x-requests-remaining")
    used = g("x-requests-used")
    last = g("x-requests-last")
    return rem, used, 0 if last is None else last


def http_get(url: str, params: dict, charged: bool, meta: dict, timeout: int = 30) -> tuple[object, dict]:
    """GET JSON. `charged` means this endpoint can cost credits (enforce budget)."""
    q = dict(params)
    # Keep the key out of raised error strings and logs.
    q["apiKey"] = _api_key()
    full = url + "?" + urllib.parse.urlencode(q)
    # Redacted URL for errors
    redacted = url + "?" + urllib.parse.urlencode({k: v for k, v in q.items() if k != "apiKey"})
    if charged:
        n_mk = 0
        if params.get("markets"):
            n_mk = len(str(params["markets"]).split(","))
        n_reg = 1
        if params.get("regions"):
            n_reg = len(str(params["regions"]).split(","))
        assert_budget(max(1, n_mk * n_reg) if n_mk else 1)
    req = urllib.request.Request(full, headers={"Accept": "application/json", "User-Agent": "parlay-locks-odds/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            usage = {
                "remaining": resp.headers.get("x-requests-remaining"),
                "used": resp.headers.get("x-requests-used"),
                "last": resp.headers.get("x-requests-last"),
            }
            rem, used, last = _headers_usage(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        raise OddsError(f"Odds API HTTP {e.code} for {redacted}: {body}") from None
    except urllib.error.URLError as e:
        raise OddsError(f"Odds API request failed for {redacted}: {e.reason}") from None
    if charged:
        _record_call(last, rem, used, {**meta, "url": redacted})
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise OddsError(f"Odds API returned non-JSON for {redacted}") from e
    return data, {"remaining_month": rem, "used_month": used, "cost": last if charged else 0}


def _cache_key(event_id: str, markets: list[str], regions: str) -> Path:
    mk = ",".join(markets)
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", f"{event_id}_{regions}_{mk}")
    return cache_dir() / f"{safe}.json"


def fetch_events(sport: str) -> tuple[list[dict], dict]:
    sk = resolve_sport(sport)
    data, usage = http_get(f"{API_BASE}/sports/{sk}/events", {}, charged=False, meta={"endpoint": "events", "sport": sk})
    if not isinstance(data, list):
        raise OddsError("unexpected events payload")
    return data, usage


def fetch_event_odds(sport: str, event_id: str, markets: list[str], *, regions: str = "us", use_cache: bool = True) -> tuple[dict, dict]:
    sk = resolve_sport(sport) if not sport.startswith(("basketball_", "icehockey_", "americanfootball_", "baseball_")) else sport
    markets = resolve_markets(markets)
    if not markets:
        raise OddsError("at least one market is required")
    if not event_id:
        raise OddsError("event id is required")
    cpath = _cache_key(event_id, markets, regions)
    if use_cache and cpath.exists() and time.time() - cpath.stat().st_mtime < CACHE_TTL_S:
        payload = json.loads(cpath.read_text(encoding="utf-8"))
        return payload["data"], {**payload.get("usage", {}), "cached": True, "cost": 0}
    params = {
        "regions": regions,
        "markets": ",".join(markets),
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    data, usage = http_get(
        f"{API_BASE}/sports/{sk}/events/{event_id}/odds",
        params,
        charged=True,
        meta={"endpoint": "event_odds", "sport": sk, "event_id": event_id, "markets": markets, "regions": regions},
    )
    if not isinstance(data, dict):
        raise OddsError("unexpected odds payload")
    cache_dir().mkdir(parents=True, exist_ok=True)
    cpath.write_text(json.dumps({"data": data, "usage": usage, "cached_at": now_et_iso()}), encoding="utf-8")
    usage = {**usage, "cached": False}
    return data, usage


def format_event(ev: dict) -> dict:
    ct = ev.get("commence_time")
    start_et = None
    if ct:
        dt = datetime.fromisoformat(ct.replace("Z", "+00:00")).astimezone(ET)
        start_et = dt.isoformat(timespec="seconds")
    return {
        "id": ev.get("id"),
        "sport_key": ev.get("sport_key"),
        "sport_title": ev.get("sport_title"),
        "event": f"{ev.get('away_team')} @ {ev.get('home_team')}",
        "away_team": ev.get("away_team"),
        "home_team": ev.get("home_team"),
        "commence_time": ct,
        "start_time": start_et,
    }


def resolve_event(sport: str, *, event: str | None = None, event_id: str | None = None) -> dict:
    events, _ = fetch_events(sport)
    if event_id:
        for ev in events:
            if ev.get("id") == event_id:
                return ev
        # The event list only has upcoming; still allow a direct id (odds endpoint may 404).
        return {"id": event_id, "away_team": None, "home_team": None, "sport_key": resolve_sport(sport)}
    if not event:
        raise OddsError("pass --event 'AWAY @ HOME' or --event-id")
    hits = [ev for ev in events if event_matches(ev, event)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        names = ", ".join(f"{e['away_team']} @ {e['home_team']}" for e in events[:12]) or "(none)"
        raise OddsError(f"no matching event for {event!r}. Upcoming: {names}")
    names = ", ".join(f"{e['id'][:8]} {e['away_team']} @ {e['home_team']}" for e in hits)
    raise OddsError(f"event {event!r} is ambiguous ({len(hits)} matches): {names}")


# --------------- pricing ---------------

def _book_two_ways(odds: dict) -> dict:
    """(player, market, line) -> {book_key: {over, under, last_update, book_title}} at exact line."""
    out = {}
    for b in odds.get("bookmakers") or []:
        bkey = b.get("key") or b.get("title") or "unknown"
        btitle = b.get("title") or bkey
        for m in b.get("markets") or []:
            mk = m.get("key")
            last = m.get("last_update") or b.get("last_update")
            sides = {}
            for o in m.get("outcomes") or []:
                name = (o.get("name") or "").strip()
                if name not in ("Over", "Under"):
                    continue
                player = o.get("description") or ""
                if o.get("point") is None:
                    continue
                line = float(o["point"])
                sides.setdefault((player, mk, line), {})[name] = o.get("price")
            for k, v in sides.items():
                if "Over" in v and "Under" in v:
                    out.setdefault(k, {})[bkey] = {
                        "over": v["Over"],
                        "under": v["Under"],
                        "last_update": last,
                        "book_title": btitle,
                    }
    return out


def most_booked_line(by_line: dict) -> float | None:
    """by_line: line -> book map. Prefer the line with the most two-way books; .5 lines break ties."""
    if not by_line:
        return None
    def key(line):
        books = by_line[line]
        is_half = abs(line * 2 - round(line * 2)) < 1e-9 and abs(line % 1 - 0.5) < 1e-9
        return (len(books), 1 if is_half else 0, -abs(line))
    return max(by_line, key=key)


def price_from_odds(
    odds: dict,
    *,
    market: str | None = None,
    player: str | None = None,
    line: float | None = None,
    pick: str | None = None,
    price_ts: str | None = None,
) -> list[dict]:
    """Return priced legs from a single event-odds payload.

    If player/line/market/pick are given, filter to those. If line is omitted,
    use the most-booked line per player+market. Legs with fewer than 2 books
    are returned with ok=False.
    """
    mk_filter = resolve_market(market) if market else None
    pick_n = (pick or "").strip().lower() or None
    if pick_n and pick_n not in ("over", "under", "more", "less"):
        raise OddsError("pick must be over/under/more/less")
    side = None
    if pick_n in ("over", "more"):
        side = "over"
    elif pick_n in ("under", "less"):
        side = "under"
    ts = price_ts or now_et_iso()
    two = _book_two_ways(odds)
    # group by (player, market) -> line -> books
    grouped = {}
    for (pname, mk, ln), books in two.items():
        if mk_filter and mk != mk_filter:
            continue
        if player and not players_match(pname, player):
            continue
        grouped.setdefault((pname, mk), {})[ln] = books

    ev_label = f"{odds.get('away_team')} @ {odds.get('home_team')}" if odds.get("away_team") else odds.get("id")
    results = []
    for (pname, mk), by_line in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
        if line is not None:
            use_line = float(line)
            books = by_line.get(use_line) or {}
            # tolerate tiny float noise
            if not books:
                for ln, bk in by_line.items():
                    if abs(ln - use_line) < 1e-9:
                        use_line, books = ln, bk
                        break
        else:
            use_line = most_booked_line(by_line)
            books = by_line.get(use_line) or {}
        rec = _summarize_books(
            books,
            player=pname,
            market=mk,
            line=use_line if use_line is not None else line,
            side=side,
            event=ev_label,
            sport=odds.get("sport_title") or odds.get("sport_key"),
            event_id=odds.get("id"),
            start=odds.get("commence_time"),
            price_ts=ts,
            alt_lines=sorted(by_line.keys()),
        )
        results.append(rec)
    # If the caller named a player that we didn't find at all, say so.
    if player and not results:
        results.append({
            "ok": False,
            "error": f"no two-way prices for player {player!r} in market {mk_filter or 'any'}",
            "player": player,
            "market": mk_filter,
            "line": line,
            "event": ev_label,
            "event_id": odds.get("id"),
            "n_books": 0,
        })
    return results


def _summarize_books(books: dict, *, player, market, line, side, event, sport, event_id, start, price_ts, alt_lines) -> dict:
    n = len(books)
    fair_overs = []
    book_rows = []
    last_updates = []
    for bkey, info in books.items():
        fo, fu = devig_multiplicative(info["over"], info["under"])
        fair_overs.append(fo)
        book_rows.append({
            "book": bkey,
            "title": info.get("book_title") or bkey,
            "over": info["over"],
            "under": info["under"],
            "fair_over": round(fo, 6),
            "fair_under": round(fu, 6),
            "last_update": info.get("last_update"),
        })
        if info.get("last_update"):
            last_updates.append(info["last_update"])
    book_rows.sort(key=lambda r: r["book"])
    ok = n >= MIN_BOOKS and line is not None
    raw_over = float(median(fair_overs)) if fair_overs else None
    rec = {
        "ok": ok,
        "player": player,
        "market": market,
        "line": line,
        "event": event,
        "event_id": event_id,
        "sport": sport,
        "start_time": (
            datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ET).isoformat(timespec="seconds")
            if start else None
        ),
        "n_books": n,
        "price_ts": price_ts,
        "price_method": "per_book_median",
        "price_books": [
            f"{r['book']} {fmt_american(r['over'])}/{fmt_american(r['under'])}" for r in book_rows
        ],
        "books": book_rows,
        "alt_lines": alt_lines,
    }
    if raw_over is None:
        rec["error"] = f"no two-way books at line {line}"
        return rec
    if not ok:
        rec["error"] = f"need at least {MIN_BOOKS} books with a two-way price at line {line}; got {n}"
    rec["est_prob_over_raw"] = round(raw_over, 6)
    rec["est_prob_under_raw"] = round(1 - raw_over, 6)
    # Side-specific output (shade unders only)
    def pack(which: str) -> dict:
        raw = raw_over if which == "over" else (1 - raw_over)
        shaded = raw if which == "over" else max(0.0, raw - UNDER_SHADE)
        return {
            "pick": which,
            "est_prob_raw": round(raw, 6),
            "est_prob": round(shaded, 6),
            "shaded": which == "under",
        }
    if side:
        rec.update(pack(side))
    else:
        rec["over"] = pack("over")
        rec["under"] = pack("under")
        # Convenience: default pick is the side with raw >= 0.5 (no shade on the choice itself)
        rec["preferred"] = "over" if raw_over >= 0.5 else "under"
        rec.update(pack(rec["preferred"]))
    if last_updates:
        rec["books_last_update_min"] = min(last_updates)
    return rec


def price_query(
    sport: str,
    *,
    event: str | None = None,
    event_id: str | None = None,
    markets=None,
    market: str | None = None,
    player: str | None = None,
    line: float | None = None,
    pick: str | None = None,
    use_cache: bool = True,
) -> dict:
    ev = resolve_event(sport, event=event, event_id=event_id)
    mks = resolve_markets(markets if markets is not None else market)
    if not mks:
        raise OddsError("pass --market or --markets")
    odds, usage = fetch_event_odds(ev.get("sport_key") or sport, ev["id"], mks, use_cache=use_cache)
    merged = {**ev, **{k: odds.get(k) for k in ("id", "away_team", "home_team", "commence_time", "sport_key", "sport_title") if odds.get(k) is not None}}
    # If a single market was requested, filter to it; otherwise return every requested market.
    mk_filter = mks[0] if len(mks) == 1 else (resolve_market(market) if market else None)
    legs = price_from_odds(odds, market=mk_filter, player=player, line=line, pick=pick)
    st = budget_status()
    return {
        "event": format_event(merged),
        "markets": mks,
        "legs": legs,
        "usage": usage,
        "budget": st,
    }


# --------------- lock-time prices on slips ---------------

def _load_json(path: str) -> tuple[dict, str]:
    p = path if os.path.isabs(path) else os.path.join(REPO, path)
    return json.loads(Path(p).read_text(encoding="utf-8")), p


def lock_prices_from_candidates(slips_path: str, *, candidates_path: str | None = None, dry_run: bool = False) -> dict:
    """Copy candidate price fields onto a slips file as lock_prices (no API call)."""
    slips, sp = _load_json(slips_path)
    run = slips.get("run")
    if not run:
        raise OddsError("slips file has no run")
    cp = candidates_path or os.path.join(REPO, "data", "candidates", f"{run}.json")
    cands, cp = _load_json(cp)
    by_id = {lg["id"]: lg for lg in cands.get("legs") or []}
    used = []
    for s in slips.get("slips") or []:
        used.extend(s.get("legs") or [])
    # unique, keep order
    seen, ids = set(), []
    for i in used:
        if i not in seen:
            ids.append(i)
            seen.add(i)
    lock = {}
    missing = []
    for lid in ids:
        lg = by_id.get(lid)
        if not lg:
            missing.append(lid)
            continue
        lock[lid] = {
            "player": lg.get("player"),
            "market": lg.get("market"),
            "line": lg.get("line"),
            "pick": lg.get("pick"),
            "est_prob": lg.get("est_prob"),
            "est_prob_raw": lg.get("est_prob_raw"),
            "price_ts": lg.get("price_ts"),
            "n_books": lg.get("n_books"),
            "price_books": lg.get("price_books"),
            "price_method": lg.get("price_method"),
            "source": "candidates",
        }
    out = {
        "lock_ts": now_et_iso(),
        "lock_source": "candidates",
        "lock_candidates": os.path.relpath(cp, REPO) if cp.startswith(REPO) else cp,
        "lock_prices": lock,
        "missing_legs": missing,
    }
    if not dry_run:
        slips["lock_ts"] = out["lock_ts"]
        slips["lock_source"] = out["lock_source"]
        slips["lock_candidates"] = out["lock_candidates"]
        slips["lock_prices"] = lock
        Path(sp).write_text(json.dumps(slips, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out["wrote"] = sp
    else:
        out["wrote"] = None
    return out


# --------------- CLI ---------------

def _dump(obj) -> None:
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def cmd_events(a) -> int:
    events, usage = fetch_events(a.sport)
    rows = [format_event(ev) for ev in events]
    if a.event:
        rows = [r for r, ev in zip(rows, events) if event_matches(ev, a.event)]
    _dump({"sport": resolve_sport(a.sport), "n": len(rows), "events": rows, "usage": {**usage, "cost": 0}, "budget": budget_status()})
    return 0


def cmd_price(a) -> int:
    result = price_query(
        a.sport,
        event=a.event,
        event_id=a.event_id,
        markets=a.markets or a.market,
        market=a.market,
        player=a.player,
        line=a.line,
        pick=a.pick,
        use_cache=not a.no_cache,
    )
    if a.ok_only:
        result["legs"] = [lg for lg in result["legs"] if lg.get("ok")]
    _dump(result)
    # Non-zero if the caller asked for a specific player/line and it failed.
    if a.player and not any(lg.get("ok") for lg in result["legs"]):
        return 2
    return 0


def cmd_lock(a) -> int:
    out = lock_prices_from_candidates(a.path, candidates_path=a.candidates, dry_run=a.dry_run)
    _dump(out)
    return 0


def cmd_budget(a) -> int:
    _dump(budget_status())
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="odds.py", description="Parlay Locks Odds API pricing (paper only)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    e = sp.add_parser("events", help="list upcoming events (free)")
    e.add_argument("--sport", required=True)
    e.add_argument("--event", help="optional filter, e.g. 'IND @ HOU' or 'Colts'")
    e.set_defaults(fn=cmd_events)

    p = sp.add_parser("price", help="devig median price for an event/market")
    p.add_argument("--sport", required=True)
    p.add_argument("--event", help="AWAY @ HOME or a team name")
    p.add_argument("--event-id")
    p.add_argument("--market", help="Assists, SOG, receptions, …")
    p.add_argument("--markets", help="comma-separated markets (1 credit each)")
    p.add_argument("--player")
    p.add_argument("--line", type=float)
    p.add_argument("--pick", choices=["over", "under", "more", "less"])
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--ok-only", action="store_true")
    p.set_defaults(fn=cmd_price)

    l = sp.add_parser("lock", help="stamp lock-time prices onto a slips file from its candidates (no API)")
    l.add_argument("path")
    l.add_argument("--candidates")
    l.add_argument("--dry-run", action="store_true")
    l.set_defaults(fn=cmd_lock)

    b = sp.add_parser("budget", help="show daily Odds API credit budget")
    b.set_defaults(fn=cmd_budget)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except OddsError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
