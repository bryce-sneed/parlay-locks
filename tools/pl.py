#!/usr/bin/env python3
"""Parlay Locks repo helper (box side). Paper dry run only.

  pl.py feed --agent parlay-locks --action draft_candidates --run 2026-11-12-morning --text "..." [--to panel] [--ref data/...]
  pl.py validate data/candidates/2026-11-12-morning.json      # light schema check by file location
  pl.py status [RUN]                                          # what exists for a run (candidates/votes/panel/slips/results)
  pl.py publish -m "parlay-locks: candidates 2026-11-12-morning"   # git add data, commit, pull --rebase, push (retries)
Env: PL_REPO (default /home/box/parlay-locks/repo)
"""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

REPO = os.environ.get("PL_REPO", "/home/box/parlay-locks/repo")
ET = ZoneInfo("America/New_York")
AGENTS = {"parlay-locks", "panel", "claude", "codex", "cursor", "system"}
ACTIONS = {"draft_candidates", "pickup", "handoff", "vote", "revise", "consensus", "finalize_slips", "grade", "note", "error", "setup"}
RUN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(morning|pregame)$")

def now_et():
    return datetime.now(ET).isoformat(timespec="seconds")

def git(*a, check=True):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True, check=check)

def cmd_feed(a):
    if a.agent not in AGENTS: sys.exit(f"agent must be one of {sorted(AGENTS)}")
    if a.action not in ACTIONS: sys.exit(f"action must be one of {sorted(ACTIONS)}")
    o = {"ts": now_et(), "agent": a.agent, "action": a.action}
    if a.to: o["to"] = a.to
    if a.run: o["run"] = a.run
    o["text"] = a.text
    if a.ref: o["ref"] = a.ref
    with open(os.path.join(REPO, "data", "feed.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(json.dumps(o, ensure_ascii=False))

def check(cond, msg, errs):
    if not cond: errs.append(msg)

def cmd_validate(a):
    p = a.path if os.path.isabs(a.path) else os.path.join(REPO, a.path)
    d = json.load(open(p, encoding="utf-8"))
    rel = os.path.relpath(p, REPO); errs = []
    if "/candidates/" in "/" + rel:
        check(d.get("schema") == "candidates/v1", "schema must be candidates/v1", errs)
        check(RUN_RE.match(d.get("run", "")), "run must be YYYY-MM-DD-morning|pregame", errs)
        check(d.get("snapshot") in ("morning", "pregame"), "snapshot", errs)
        ids = set()
        for i, l in enumerate(d.get("legs", [])):
            for k in ("id", "sport", "event", "player", "market", "line", "pick", "reasoning"):
                check(k in l, f"leg {i} missing {k}", errs)
            check(re.match(r"^L\d+$", str(l.get("id", ""))), f"leg {i} id must look like L1", errs)
            check(l.get("id") not in ids, f"duplicate leg id {l.get('id')}", errs); ids.add(l.get("id"))
            check(isinstance(l.get("line"), (int, float)), f"leg {i} line must be a number", errs)
            check(l.get("pick") in ("over", "under", "more", "less"), f"leg {i} pick", errs)
            if "est_prob" in l: check(0 <= l["est_prob"] <= 1, f"leg {i} est_prob 0..1", errs)
        check(0 < len(d.get("legs", [])) <= 15, "1-15 legs", errs)
    elif "/slips/" in "/" + rel:
        check(d.get("schema") == "slips/v1", "schema must be slips/v1", errs)
        for s in d.get("slips", []):
            for k in ("id", "size", "platform", "stake", "payout_multiplier", "legs"): check(k in s, f"slip missing {k}", errs)
            check(s.get("size") in (2, 3, 5) and len(s.get("legs", [])) == s.get("size"), f"slip {s.get('id')} size/legs mismatch", errs)
    elif "/results/" in "/" + rel:
        check(d.get("schema") == "results/v1", "schema must be results/v1", errs)
        for l in d.get("legs", []): check(l.get("outcome") in ("hit", "miss", "push", "void", "pending"), f"leg {l.get('leg_id')} outcome", errs)
        for s in d.get("slips", []):
            for k in ("id", "size", "stake", "outcome", "payout", "pnl"): check(k in s, f"slip {s.get('id')} missing {k}", errs)
    print("OK" if not errs else "INVALID:\n- " + "\n- ".join(errs)); sys.exit(1 if errs else 0)

def cmd_status(a):
    git("pull", "-q", "--rebase", "origin", "main", check=False)
    runs = [a.run] if a.run else sorted({f[:-5] for f in os.listdir(os.path.join(REPO, "data", "candidates")) if RUN_RE.match(f[:-5])})[-4:]
    for r in runs:
        vd = os.path.join(REPO, "data", "votes", r)
        print(r, {"candidates": os.path.exists(f"{REPO}/data/candidates/{r}.json"),
                  "votes": sorted(os.listdir(vd)) if os.path.isdir(vd) else [],
                  "slips": os.path.exists(f"{REPO}/data/slips/{r}.json"),
                  "results": os.path.exists(f"{REPO}/data/results/{r}.json")})

def cmd_publish(a):
    git("add", "-A", "data")
    c = git("-c", "user.name=Parlay Locks (Grok Bot)", "-c", "user.email=57764294+bryce-sneed@users.noreply.github.com", "commit", "-q", "-m", a.message, check=False)
    if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr): sys.exit(c.stderr)
    for i in range(1, 5):
        git("pull", "-q", "--rebase", "origin", "main", check=False)
        p = git("push", "-q", "origin", "HEAD:main", check=False)
        if p.returncode == 0: print("pushed"); return
        time.sleep(4 * i)
    sys.exit("push failed: " + p.stderr)

ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
f = sp.add_parser("feed"); f.add_argument("--agent", default="parlay-locks"); f.add_argument("--action", required=True); f.add_argument("--text", required=True)
f.add_argument("--to"); f.add_argument("--run"); f.add_argument("--ref"); f.set_defaults(fn=cmd_feed)
v = sp.add_parser("validate"); v.add_argument("path"); v.set_defaults(fn=cmd_validate)
s = sp.add_parser("status"); s.add_argument("run", nargs="?"); s.set_defaults(fn=cmd_status)
p = sp.add_parser("publish"); p.add_argument("-m", "--message", required=True); p.set_defaults(fn=cmd_publish)
a = ap.parse_args(); a.fn(a)
