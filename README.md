# Parlay Locks — multi-model paper panel

**Paper only (week 1 Nov 12–18, week 2 Nov 19–25, 2026). No real money.**

- **Parlay Locks** (Grok Bot) researches the slate and drafts candidate legs → `data/candidates/`
- **Panel runner** on Sneed_PC (`pc/run-panel.ps1`, Task Scheduler 10:30 + 17:30 ET) asks **Claude Code** and **Cursor Agent** to vote keep/cut (Codex is optional via `-Models`) → `data/votes/<run>/`. A named-error cut from either default model vetoes the leg.
- Parlay Locks finalizes 2/3/5-leg slips → `data/slips/`, grades them → `data/results/`
- Every action/message between agents → `data/feed.jsonl` (append-only, `merge=union`)
- Dashboard: `index.html` + `app.js` on GitHub Pages (static, no build).

Schemas: [`schemas/`](schemas). Run ids: `YYYY-MM-DD-morning` / `YYYY-MM-DD-pregame`.

Box helper: `tools/pl.py` (`pl events`, `pl price`, `pl lock`, `pl budget`, `pl applines`, plus feed/validate/publish). Pricing uses The Odds API with a daily credit cap; see the box runbook.
