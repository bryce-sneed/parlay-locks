#!/usr/bin/env python3
"""Match Parlay Locks legs to public PrizePicks / Underdog board lines.

Public, read-only, no login. If a host returns 403, a captcha/JS challenge,
App Check, or a similar block, we report it and stop — no workarounds.

  applines.py data/candidates/RUN.json
  applines.py --player "Myles Turner" --market Assists --line 1.5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
urllib_request = __import__("urllib.request")
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = os.environ.get("PL_REPO", "/home/box/parlay-locks/repo")
TIMEOUT = 20
UA = "parlay-locks-applines/1 (paper dry-run; read-only)"

# PrizePicks league_ids (commonly cited; the request is still public/unauthenticated).
PP_LEAGUES = {"NBA": "7", "NHL": "8", "NFL": "9", "MLB": "2", "WNBA": "3"}
PP_URL = "https://api.prizepicks.com/projections"

# Public Underdog endpoints that have worked without auth in the past.
UD_URLS = [
    "https://api.underdogfantasy.com/beta/v5/over_under_lines",
    "https://api.underdogfantasy.com/v5/over_under_lines",
    "https://api.underdogfantasy.com/beta/v3/over_under_lines",
]


def now_et_iso() -> str:
    return datetime.now(ET).isoformat(timespec="seconds")


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("é", "e").replace("è", "e").replace("ö", "o")
    s = re.sub(r"[.']", "", s)
    s = re.sub(r"\b(jr|sr|iii|ii|iv)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def names_match(a: str, b: str) -> bool:
    ka, kb = _norm(a), _norm(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    pa, pb = ka.split(), kb.split()
    if pa[-1] == pb[-1] and (len(pa) == 1 or len(pb) == 1 or pa[0][0] == pb[0][0]):
        return True
    return False


def markets_match(a: str, b: str) -> bool:
    aliases = {
        "points": {"points", "pts", "point"},
        "rebounds": {"rebounds", "reb", "rebs", "rebound"},
        "assists": {"assists", "ast", "asts", "assist"},
        "threes": {"threes", "3pm", "3s", "three pointers", "3-pointers", "three-pointers", "fg3m"},
        "blocks": {"blocks", "blk", "blocked shots"},
        "steals": {"steals", "stl"},
        "turnovers": {"turnovers", "to"},
        "shots on goal": {"shots on goal", "sog", "shots", "player_shots_on_goal"},
        "goals": {"goals"},
        "receptions": {"receptions", "rec", "player_receptions"},
        "receiving yards": {"receiving yards", "rec yds", "rec yards", "player_reception_yds"},
        "rush attempts": {"rush attempts", "carries", "rush att", "player_rush_attempts"},
        "rush yards": {"rush yards", "rush yds", "player_rush_yds"},
        "pass yards": {"pass yards", "pass yds", "passing yards", "player_pass_yds"},
        "pass attempts": {"pass attempts", "player_pass_attempts"},
        "pass completions": {"pass completions", "completions", "player_pass_completions"},
    }
    na, nb = _norm(a).replace("player_", "").replace("_", " "), _norm(b).replace("player_", "").replace("_", " ")
    if na == nb:
        return True
    for group in aliases.values():
        if na in group and nb in group:
            return True
    return False


def classify_block(status: int, body: str, content_type: str = "") -> str | None:
    """Return a short reason if this looks like a bot/auth wall, else None."""
    text = (body or "")[:2000]
    low = text.lower()
    ct = (content_type or "").lower()
    if status in (401, 403, 407, 429, 503):
        if any(s in low for s in ("captcha", "cf-browser", "attention required", "enable js", "enable javascript",
                                  "app check", "datadome", "cloudflare", "access denied", "forbidden")):
            return f"blocked HTTP {status} (bot/captcha or app-check wall)"
        return f"blocked HTTP {status}"
    if status == 426:
        return "blocked HTTP 426 (upgrade/app-version required)"
    if "app check token" in low or "app-check" in low:
        return "blocked: App Check token required"
    if "please enable js" in low or "enable javascript" in low or "cf-browser-verification" in low:
        return "blocked: JavaScript/captcha challenge (not JSON)"
    if status == 200 and "html" in ct and ("<html" in low or "enable js" in low):
        return "blocked: HTML challenge page instead of JSON"
    return None


def http_get(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    q = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    h = {"Accept": "application/json, text/plain, */*", "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(q, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            status = resp.status
            ct = resp.headers.get("Content-Type", "")
            body = raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        body = raw.decode("utf-8", "replace")
        status = e.code
        ct = e.headers.get("Content-Type", "") if e.headers else ""
    except urllib.error.URLError as e:
        return {"ok": False, "blocked": True, "error": f"request failed: {e.reason}", "url": url, "status": None}
    block = classify_block(status, body, ct)
    if block:
        return {"ok": False, "blocked": True, "error": block, "url": q, "status": status, "snippet": body[:240]}
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Non-JSON that didn't trip the HTML/captcha classifier
        return {"ok": False, "blocked": False, "error": f"non-JSON response HTTP {status}", "url": q, "status": status, "snippet": body[:240]}
    if status != 200:
        return {"ok": False, "blocked": False, "error": f"HTTP {status}", "url": q, "status": status, "data": data}
    return {"ok": True, "blocked": False, "url": q, "status": status, "data": data}


def fetch_prizepicks(league_id: str | None = None) -> dict:
    params = {"single_stat": "true", "per_page": "250"}
    if league_id:
        params["league_id"] = str(league_id)
    return http_get(PP_URL, params)


def parse_prizepicks(payload: dict) -> list[dict]:
    data = payload.get("data") or []
    included = payload.get("included") or []
    players = {}
    for item in included:
        if item.get("type") in ("new_player", "player"):
            attrs = item.get("attributes") or {}
            players[item.get("id")] = {
                "name": attrs.get("display_name") or attrs.get("name") or "",
                "team": attrs.get("team") or attrs.get("team_name") or "",
            }
    rows = []
    for proj in data:
        attrs = proj.get("attributes") or {}
        rel = ((proj.get("relationships") or {}).get("new_player") or {}).get("data") or {}
        pid = rel.get("id")
        player = players.get(pid, {})
        odds_type = (attrs.get("odds_type") or attrs.get("projection_type") or "standard") or "standard"
        line = attrs.get("line_score")
        try:
            line = float(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        rows.append({
            "platform": "prizepicks",
            "player": player.get("name") or "",
            "team": player.get("team") or "",
            "market": attrs.get("stat_type") or attrs.get("stat_display_name") or "",
            "line": line,
            "odds_type": str(odds_type).lower(),
            "start_time": attrs.get("start_time"),
            "is_goblin": str(odds_type).lower() == "goblin" or bool(attrs.get("goblin")),
            "is_demon": str(odds_type).lower() == "demon" or bool(attrs.get("demon")),
        })
    return rows


def fetch_underdog() -> dict:
    last = None
    for url in UD_URLS:
        res = http_get(url)
        last = res
        if res.get("ok") or res.get("blocked"):
            res["tried"] = url
            return res
    return last or {"ok": False, "blocked": False, "error": "no Underdog endpoint attempted"}


def _walk_ud_lines(data) -> list[dict]:
    """Best-effort parse of Underdog's over_under_lines JSON, which has changed shapes."""
    rows = []

    def as_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # Common shape: {over_under_lines: [{over_under: {appearance:{player:{...}}, stat_info?}, options:[], ...}]}
    if isinstance(data, dict):
        lines = data.get("over_under_lines") or data.get("data") or data.get("lines") or []
        players = {}
        appearances = {}
        over_unders = {}
        # Sometimes it's a JSON:API-like include
        for item in data.get("included") or []:
            typ = item.get("type")
            if typ in ("player", "players"):
                players[item.get("id")] = item.get("attributes") or item
            elif typ in ("appearance", "appearances"):
                appearances[item.get("id")] = item.get("attributes") or item
            elif typ in ("over_under", "over_unders"):
                over_unders[item.get("id")] = item.get("attributes") or item
        if isinstance(lines, list):
            for ln in lines:
                if not isinstance(ln, dict):
                    continue
                attrs = ln.get("attributes") or ln
                ou = ln.get("over_under") or over_unders.get(
                    ((ln.get("relationships") or {}).get("over_under") or {}).get("data", {}).get("id")
                ) or {}
                if not isinstance(ou, dict):
                    ou = {}
                appearance = ou.get("appearance") or {}
                player = ""
                team = ""
                if isinstance(appearance, dict):
                    pobj = appearance.get("player") or appearance
                    if isinstance(pobj, dict):
                        player = pobj.get("display_name") or pobj.get("name") or pobj.get("first_name", "")
                        if pobj.get("last_name") and " " not in str(player):
                            player = f"{pobj.get('first_name','')} {pobj.get('last_name','')}".strip()
                        team = (appearance.get("team") or pobj.get("team") or {}).get("abbr") if isinstance(appearance.get("team") or pobj.get("team"), dict) else (appearance.get("team") or "")
                market = (
                    ou.get("stat_name") or ou.get("title") or attrs.get("stat") or attrs.get("display_stat")
                    or (ou.get("options") or [{}])[0].get("choice") if False else (ou.get("stat_name") or ou.get("title") or attrs.get("choice_display") or "")
                )
                # line / payout modifiers
                line = as_float(attrs.get("stat_value") or attrs.get("line") or ou.get("stat_value"))
                payout = attrs.get("payout_multiplier") or attrs.get("multiplier") or ou.get("payout_multiplier")
                choice = (attrs.get("choice") or attrs.get("type") or "").lower()
                # Goblin/Demon analogues on Underdog are often lower/higher multiplier picks (0.5x / 1.5x etc.)
                try:
                    mult = float(payout) if payout is not None else 1.0
                except (TypeError, ValueError):
                    mult = 1.0
                rows.append({
                    "platform": "underdog",
                    "player": player or "",
                    "team": team or "",
                    "market": market or "",
                    "line": line,
                    "odds_type": "standard" if abs(mult - 1.0) < 1e-6 else f"multiplier_{mult}",
                    "payout_multiplier": mult,
                    "choice": choice,
                    "is_goblin": mult < 1.0,  # easier, reduced payout (Goblin-like)
                    "is_demon": mult > 1.0,   # harder, boosted payout (Demon-like)
                })
    return rows


def fetch_boards(sports: list[str] | None = None) -> dict:
    sports = [s.upper() for s in (sports or ["NBA", "NHL", "NFL"])]
    out = {"fetched_at": now_et_iso(), "prizepicks": {}, "underdog": {}, "lines": []}
    # PrizePicks: one request per requested league (stop a league if blocked).
    pp_blocked = None
    for sp in sports:
        lid = PP_LEAGUES.get(sp)
        if not lid:
            continue
        res = fetch_prizepicks(lid)
        out["prizepicks"][sp] = {k: res[k] for k in res if k != "data"}
        if res.get("blocked"):
            pp_blocked = res
            break
        if res.get("ok"):
            rows = parse_prizepicks(res["data"])
            for r in rows:
                r["sport"] = sp
            out["lines"].extend(rows)
            out["prizepicks"][sp]["n"] = len(rows)
    if pp_blocked:
        out["prizepicks_blocked"] = pp_blocked.get("error")
    # Underdog: one board for all sports
    ud = fetch_underdog()
    out["underdog"] = {k: ud[k] for k in ud if k != "data"}
    if ud.get("blocked"):
        out["underdog_blocked"] = ud.get("error")
    elif ud.get("ok"):
        rows = _walk_ud_lines(ud["data"])
        out["lines"].extend(rows)
        out["underdog"]["n"] = len(rows)
    return out


def match_leg(leg: dict, board_lines: list[dict]) -> dict:
    player = leg.get("player") or ""
    market = leg.get("market") or ""
    line = leg.get("line")
    hits = []
    for b in board_lines:
        if player and not names_match(player, b.get("player") or ""):
            continue
        if market and b.get("market") and not markets_match(market, b.get("market") or ""):
            continue
        hits.append(b)
    flags = []
    exact = []
    mismatches = []
    modifiers = []
    for h in hits:
        rec = {
            "platform": h.get("platform"),
            "player": h.get("player"),
            "market": h.get("market"),
            "line": h.get("line"),
            "odds_type": h.get("odds_type"),
            "is_goblin": bool(h.get("is_goblin")),
            "is_demon": bool(h.get("is_demon")),
        }
        if h.get("is_goblin") or h.get("is_demon"):
            modifiers.append(rec)
            kind = "goblin" if h.get("is_goblin") else "demon"
            flags.append(f"{h.get('platform')} {kind} line {h.get('line')}")
        if line is not None and h.get("line") is not None and abs(float(h["line"]) - float(line)) < 1e-9:
            exact.append(rec)
        elif h.get("line") is not None:
            mismatches.append(rec)
            flags.append(f"{h.get('platform')} line {h.get('line')} != drafted {line}")
    status = "unmatched"
    if exact and not mismatches:
        status = "match"
    elif exact and mismatches:
        status = "partial"  # matched on one platform/type, different on another
    elif mismatches:
        status = "mismatch"
    elif modifiers:
        status = "modifier"
    return {
        "leg_id": leg.get("id"),
        "player": player,
        "market": market,
        "line": line,
        "pick": leg.get("pick"),
        "status": status,
        "flags": flags,
        "exact": exact,
        "mismatches": mismatches,
        "modifiers": modifiers,
        "n_board_hits": len(hits),
    }


def check_candidates(path: str) -> dict:
    p = path if os.path.isabs(path) else os.path.join(REPO, path)
    cands = json.loads(Path(p).read_text(encoding="utf-8"))
    sports = sorted({(lg.get("sport") or "").upper() for lg in cands.get("legs") or [] if lg.get("sport")})
    board = fetch_boards(sports or None)
    matches = [match_leg(lg, board.get("lines") or []) for lg in cands.get("legs") or []]
    blocked = []
    if board.get("prizepicks_blocked"):
        blocked.append(f"PrizePicks: {board['prizepicks_blocked']}")
    if board.get("underdog_blocked"):
        blocked.append(f"Underdog: {board['underdog_blocked']}")
    return {
        "run": cands.get("run"),
        "path": p,
        "fetched_at": board.get("fetched_at"),
        "blocked": blocked,
        "board_counts": {
            "prizepicks": sum(1 for r in board.get("lines") or [] if r.get("platform") == "prizepicks"),
            "underdog": sum(1 for r in board.get("lines") or [] if r.get("platform") == "underdog"),
        },
        "prizepicks": board.get("prizepicks"),
        "underdog": board.get("underdog"),
        "matches": matches,
        "n_mismatch": sum(1 for m in matches if m["status"] in ("mismatch", "partial")),
        "n_modifier": sum(1 for m in matches if m["modifiers"]),
        "n_unmatched": sum(1 for m in matches if m["status"] == "unmatched"),
        "n_match": sum(1 for m in matches if m["status"] == "match"),
    }


def cmd_check(a) -> int:
    if a.path:
        out = check_candidates(a.path)
    else:
        board = fetch_boards()
        fake = {"id": "Q1", "player": a.player, "market": a.market, "line": a.line, "pick": a.pick}
        out = {
            "fetched_at": board.get("fetched_at"),
            "blocked": [x for x in [board.get("prizepicks_blocked") and f"PrizePicks: {board['prizepicks_blocked']}",
                                    board.get("underdog_blocked") and f"Underdog: {board['underdog_blocked']}"] if x],
            "board_counts": {
                "prizepicks": sum(1 for r in board.get("lines") or [] if r.get("platform") == "prizepicks"),
                "underdog": sum(1 for r in board.get("lines") or [] if r.get("platform") == "underdog"),
            },
            "prizepicks": board.get("prizepicks"),
            "underdog": board.get("underdog"),
            "matches": [match_leg(fake, board.get("lines") or [])] if a.player else [],
        }
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    # 0 = boards readable (even if some legs unmatched); 2 = blocked
    if out.get("blocked") and out.get("board_counts", {}).get("prizepicks", 0) == 0 and out.get("board_counts", {}).get("underdog", 0) == 0:
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Match legs to public PrizePicks/Underdog boards (read-only)")
    ap.add_argument("path", nargs="?", help="candidates JSON to check")
    ap.add_argument("--player")
    ap.add_argument("--market")
    ap.add_argument("--line", type=float)
    ap.add_argument("--pick")
    ap.set_defaults(fn=cmd_check)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
