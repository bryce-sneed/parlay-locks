# Parlay Locks — multi-model paper panel

**Paper dry run only (Nov 12–18, 2026). No real money.**

- **Parlay Locks** (Grok Bot) researches the slate and drafts candidate legs → `data/candidates/`
- **Panel runner** on Sneed_PC (`pc/run-panel.ps1`, Task Scheduler 10:30 + 17:30 ET) asks **Claude Code**, **Codex** and **Cursor Agent** to vote keep/cut with confidence + reasoning → `data/votes/<run>/`
- Parlay Locks finalizes 2/3/5-leg slips → `data/slips/`, grades them → `data/results/`
- Every action/message between agents → `data/feed.jsonl` (append-only, `merge=union`)
- Dashboard: `index.html` + `app.js` on GitHub Pages (static, no build).

Schemas: [`schemas/`](schemas). Run ids: `YYYY-MM-DD-morning` / `YYYY-MM-DD-pregame`.
