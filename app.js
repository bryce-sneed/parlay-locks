/* Parlay Locks dashboard — static, reads data/ from this repo. No build step. */
'use strict';
const CFG = (() => {
  let owner = 'bryce-sneed', repo = 'parlay-locks';
  if (location.hostname.endsWith('.github.io')) {
    owner = location.hostname.split('.')[0];
    const seg = location.pathname.split('/').filter(Boolean)[0];
    if (seg) repo = seg;
  }
  return { owner, repo, branch: 'main', tz: 'America/New_York', bankroll: 20, start: '2026-11-12', end: '2026-11-18' };
})();
const MODELS = ['claude', 'codex', 'cursor'];
const AG = {
  'parlay-locks': { name: 'Parlay Locks', role: 'Grok Bot · researcher & drafter', s: 'PL', c: '--pl' },
  claude: { name: 'Claude', role: 'Claude Code CLI · panelist', s: 'CL', c: '--claude' },
  codex: { name: 'Codex', role: 'ChatGPT Codex CLI · panelist', s: 'CX', c: '--codex' },
  cursor: { name: 'Cursor', role: 'Cursor Agent CLI · panelist', s: 'CU', c: '--cursor' },
  panel: { name: 'Panel runner', role: 'Sneed_PC scheduler', s: 'PN', c: '--panelc' },
  system: { name: 'System', role: 'setup', s: 'SY', c: '--system' },
};
const SCHEDULE = [ // ET, daily
  [10, 15, 'Parlay Locks drafts morning candidates'], [10, 30, 'Panel votes (morning)'], [11, 0, 'Morning slips finalized'],
  [17, 15, 'Parlay Locks drafts pregame candidates'], [17, 30, 'Panel votes (pregame)'], [18, 0, 'Pregame slips finalized'],
];
const S = { files: [], feed: [], runs: {}, date: null, tab: 'slips', feedAgent: 'all', feedScope: 'date', loadedAt: null, errors: [] };

/* ---------- utils ---------- */
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const safeUrl = (u) => /^https?:\/\//i.test(String(u || '')) ? esc(u) : '#';
const pct = (x) => (x == null || isNaN(x) ? '—' : Math.round(x * 100) + '%');
const money = (x) => (x == null ? '—' : (x < 0 ? '−$' : '$') + Math.abs(x).toFixed(2));
const sgn = (x) => (x > 0 ? 'pos' : x < 0 ? 'neg' : '');
const etParts = (d = new Date()) => Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: CFG.tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(d).map((p) => [p.type, p.value]));
const todayET = () => { const p = etParts(); return `${p.year}-${p.month}-${p.day}`; };
const fmtTime = (ts) => { if (!ts) return ''; const d = new Date(ts); return isNaN(d) ? esc(ts) : d.toLocaleTimeString('en-US', { timeZone: CFG.tz, hour: 'numeric', minute: '2-digit' }) + ' ET'; };
const fmtDT = (ts) => { const d = new Date(ts); return isNaN(d) ? esc(ts) : d.toLocaleString('en-US', { timeZone: CFG.tz, weekday: 'short', month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' ET'; };
const fmtDate = (ymd) => { const d = new Date(ymd + 'T12:00:00Z'); return d.toLocaleDateString('en-US', { timeZone: 'UTC', weekday: 'short', month: 'numeric', day: 'numeric' }); };
const ghBlob = (p) => `https://github.com/${CFG.owner}/${CFG.repo}/blob/${CFG.branch}/${p}`;
const agentSpan = (a) => `<span class="agent a-${esc(a)}">${esc((AG[a] || { name: a }).name)}</span>`;
const empty = (title, body) => `<div class="empty"><b>${title}</b>${body || ''}</div>`;
const legLabel = (l) => l ? `${esc(l.player)} ${esc(l.market)} <span class="pick">${esc(l.pick)} ${esc(l.line)}</span>` : '';

/* ---------- loading ---------- */
async function fetchText(path) {
  const r = await fetch(path + '?v=' + Math.floor(Date.now() / 30000), { cache: 'no-store' });
  if (!r.ok) throw new Error(path + ' ' + r.status);
  return r.text();
}
async function fetchJSON(path) { try { return JSON.parse(await fetchText(path)); } catch (e) { S.errors.push(String(e.message || e)); return null; } }

async function listFiles(feed) {
  // Feed refs are the primary manifest (every writer logs the file it produced). The GitHub tree API fills gaps,
  // throttled to once per 5 min with ETag (304s don't count against the 60/hr unauthenticated limit).
  const key = `pl-tree-${CFG.owner}-${CFG.repo}`;
  let cache = {}; try { cache = JSON.parse(localStorage.getItem(key) || '{}'); } catch { cache = {}; }
  let paths = cache.paths || [];
  if (!cache.at || Date.now() - cache.at > 300000) {
    try {
      const r = await fetch(`https://api.github.com/repos/${CFG.owner}/${CFG.repo}/git/trees/${CFG.branch}?recursive=1`, { headers: cache.etag ? { 'If-None-Match': cache.etag } : {} });
      if (r.ok) { const j = await r.json(); paths = j.tree.filter((t) => t.type === 'blob').map((t) => t.path); cache = { paths, etag: r.headers.get('ETag'), at: Date.now() }; }
      else if (r.status === 304) cache.at = Date.now();
      localStorage.setItem(key, JSON.stringify(cache));
    } catch (e) { /* offline / rate limited: use cache + feed refs */ }
  }
  const set = new Set(paths.filter((p) => p.startsWith('data/')));
  for (const ev of feed) if (ev.ref && /^data\/.+\.json$/.test(ev.ref)) set.add(ev.ref);
  return [...set];
}

async function load() {
  S.errors = [];
  $('#status').textContent = 'Loading…';
  let feedTxt = '';
  try { feedTxt = await fetchText('data/feed.jsonl'); } catch (e) { S.errors.push('feed: ' + e.message); }
  const feed = [];
  for (const line of feedTxt.split('\n')) { const t = line.trim(); if (!t) continue; try { feed.push(JSON.parse(t)); } catch { /* skip bad line */ } }
  feed.sort((a, b) => new Date(a.ts) - new Date(b.ts));
  S.feed = feed;
  S.files = await listFiles(feed);

  const runs = {};
  const R = (id) => (runs[id] ||= { id, date: id.slice(0, 10), snapshot: id.slice(11), votes: {} });
  const jobs = [];
  for (const p of S.files) {
    let m;
    if ((m = p.match(/^data\/(candidates|slips|results)\/(\d{4}-\d{2}-\d{2}-(morning|pregame))\.json$/))) {
      const [, kind, id] = m; jobs.push(fetchJSON(p).then((j) => { if (j) R(id)[kind] = j; }));
    } else if ((m = p.match(/^data\/votes\/(\d{4}-\d{2}-\d{2}-(?:morning|pregame))\/([a-z_]+?)(-r2)?\.json$/))) {
      const [, id, who, r2] = m;
      jobs.push(fetchJSON(p).then((j) => { if (!j) return; const run = R(id); if (who === '_panel') run.panel = j; else { (run.votes[who] ||= {})[r2 ? 'r2' : 'r1'] = j; } }));
    }
  }
  for (const ev of feed) if (ev.run && /^\d{4}-\d{2}-\d{2}-(morning|pregame)$/.test(ev.run)) R(ev.run);
  await Promise.all(jobs);
  S.runs = runs;
  S.loadedAt = new Date();
  const dates = allDates();
  if (!S.date || !dates.includes(S.date)) S.date = dates.includes(todayET()) ? todayET() : dates[0];
  render();
}

function allDates() {
  const set = new Set(Object.values(S.runs).map((r) => r.date));
  set.add(todayET());
  return [...set].sort().reverse();
}
const runsFor = (date) => ['morning', 'pregame'].map((s) => S.runs[`${date}-${s}`]).filter(Boolean);
const finalVotes = (run, m) => { const v = run.votes[m]; if (!v) return null; return (v.r2 && v.r2.status !== 'error') ? v.r2 : v.r1; };
const voteFor = (doc, legId) => doc && doc.status !== 'error' ? (doc.votes || []).find((v) => v.leg_id === legId) : null;
const legResult = (run, legId) => run.results ? (run.results.legs || []).find((l) => l.leg_id === legId) : null;
const legById = (run, id) => run.cand ? (run.cand.legs || []).find((l) => l.id === id) : null;
const candOf = (run) => run.candidates;
// alias
function normRun(run) { run.cand = run.candidates; return run; }

/* ---------- header / chrome ---------- */
function nextEvent() {
  const p = etParts(); const mins = (+p.hour) * 60 + (+p.minute);
  for (const [h, m, label] of SCHEDULE) if (h * 60 + m > mins) return `${label} at ${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'} ET`;
  return `${SCHEDULE[0][2]} tomorrow at 10:15 AM ET`;
}
function renderChrome() {
  const lastEv = S.feed[S.feed.length - 1];
  $('#status').innerHTML = `Updated ${fmtTime(S.loadedAt)} · next: ${esc(nextEvent())}` + (lastEv ? ` · last activity ${fmtTime(lastEv.ts)} by ${agentSpan(lastEv.agent)}` : '');
  $('#runbar').innerHTML = allDates().map((d) => {
    const rs = runsFor(d); const n = rs.filter((r) => r.slips).length;
    return `<button class="chip ${d === S.date ? 'on' : ''}" data-date="${d}">${fmtDate(d)}${d === todayET() ? ' · today' : ''}${n ? ` · ${n} slip set${n > 1 ? 's' : ''}` : ''}</button>`;
  }).join('');
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === S.tab));
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('on', t.id === 'tab-' + S.tab));
}

function pipeline(run, snap) {
  const done = (x) => x ? 'done' : 'wait';
  const c = run && run.cand, p = run && run.panel, s = run && run.slips, r = run && run.results;
  const t = snap === 'morning' ? ['10:15', '10:30', '11:00', 'after games'] : ['5:15', '5:30', '6:00', 'after games'];
  return `<div class="pipeline">
    <span class="step ${done(c)}">① Draft ${c ? '✓ ' + fmtTime(c.created_at) : '· ~' + t[0]}</span>
    <span class="step ${done(p)}">② Panel ${p ? '✓ ' + fmtTime(p.finished_at) : '· ~' + t[1]}</span>
    <span class="step ${done(s)}">③ Slips ${s ? '✓ ' + fmtTime(s.created_at) : '· ~' + t[2]}</span>
    <span class="step ${done(r)}">④ Graded ${r ? '✓ ' + fmtTime(r.graded_at) : '· ' + t[3]}</span></div>`;
}
function voteDots(run, legId) {
  return `<span class="dots">${MODELS.map((m) => { const v = voteFor(finalVotes(run, m), legId); const d = v ? v.decision : 'none'; return `<span class="dot ${d}" title="${AG[m].name}: ${v ? esc(v.decision + ' ' + v.confidence) : 'no vote'}">${AG[m].s[0]}${AG[m].s[1].toLowerCase()}</span>`; }).join('')}</span>`;
}
function outcomeTag(o) { return o ? `<span class="outcome o-${esc(o)}">${esc(o)}</span>` : ''; }

/* ---------- Slips tab ---------- */
function renderSlips() {
  const el = $('#tab-slips'); let h = '';
  for (const snap of ['morning', 'pregame']) {
    const run = S.runs[`${S.date}-${snap}`];
    h += `<h2>${snap === 'morning' ? '☀️ Morning snapshot' : '🌙 Pregame snapshot'} <span class="mute small">${fmtDate(S.date)}</span></h2>`;
    h += pipeline(run, snap);
    if (!run || !run.slips) {
      h += empty('No slips yet for this snapshot', S.date < CFG.start ? 'The paper dry run starts Thu Nov 12, 2026.' : `Slips are finalized around ${snap === 'morning' ? '11:00 AM' : '6:00 PM'} ET after the panel votes.`);
      continue;
    }
    const resSlips = run.results ? run.results.slips || [] : [];
    h += '<div class="grid">';
    for (const s of [...run.slips.slips].sort((a, b) => a.size - b.size)) {
      const rs = resSlips.find((x) => x.id === s.id);
      h += `<div class="card"><div class="row"><div><b>${s.size}-leg</b> <span class="mute">${esc(s.platform)} ${esc(s.play_type || '')}</span></div>
        <div>${rs ? outcomeTag(rs.outcome) + ` <b class="${sgn(rs.pnl)}">${money(rs.pnl)}</b>` : `<span class="mute small">$${esc(s.stake)} → ${esc(s.payout_multiplier)}x</span>`}</div></div>`;
      for (const id of s.legs) {
        const l = legById(run, id); const lr = legResult(run, id);
        h += `<div class="leg"><div class="row"><div class="legtitle">${l ? legLabel(l) : esc(id)}</div>${lr ? outcomeTag(lr.outcome) : ''}</div>
          <div class="small mute">${l ? esc(l.sport) + ' · ' + esc(l.event) + (l.start_time ? ' · ' + fmtTime(l.start_time) : '') : ''}${lr && lr.actual != null ? ' · actual ' + esc(lr.actual) : ''} ${voteDots(run, id)}</div></div>`;
      }
      if (s.rationale) h += `<div class="small mute" style="margin-top:8px">${esc(s.rationale)}</div>`;
      h += '</div>';
    }
    h += '</div>';
    if (run.slips.notes) h += `<div class="card small"><span class="agent a-parlay-locks">Parlay Locks:</span> ${esc(run.slips.notes)}</div>`;
  }
  el.innerHTML = h;
}

/* ---------- Panel tab ---------- */
function renderPanel() {
  const el = $('#tab-panel'); let h = '';
  const rs = runsFor(S.date).filter((r) => r.cand);
  if (!rs.length) { el.innerHTML = empty('No candidate legs for ' + fmtDate(S.date) + ' yet', 'Parlay Locks drafts candidates around 10:15 AM and 5:15 PM ET; Claude, Codex and Cursor vote 15 minutes later. Every vote and its reasoning shows up here.'); return; }
  for (const run of rs) {
    const legs = run.cand.legs || [];
    const cons = run.panel ? Object.fromEntries((run.panel.consensus || []).map((c) => [c.leg_id, c])) : {};
    h += `<h2>${run.snapshot === 'morning' ? '☀️ Morning' : '🌙 Pregame'} panel <span class="mute small">${legs.length} legs · drafted ${fmtTime(run.cand.created_at)}</span></h2>`;
    if (run.cand.slate_notes) h += `<div class="card reason bl-parlay-locks"><div class="who a-parlay-locks">Parlay Locks · slate notes</div>${esc(run.cand.slate_notes)}</div>`;
    // model status cards
    h += '<div class="grid">';
    for (const m of MODELS) {
      const v1 = run.votes[m] && run.votes[m].r1, v2 = run.votes[m] && run.votes[m].r2;
      if (!v1) { h += `<div class="card reason bl-${m}"><div class="who a-${m}">${AG[m].name}</div><span class="mute small">${run.panel ? 'Did not run' : 'Waiting for the 10:30 / 5:30 panel run'}</span></div>`; continue; }
      const f = v2 && v2.status !== 'error' ? v2 : v1;
      const k = (f.votes || []).filter((v) => v.decision === 'keep').length, c = (f.votes || []).filter((v) => v.decision === 'cut').length;
      const u = f.usage || {}; const usage = u.tokens ? `${u.tokens.toLocaleString()} tokens` : (u.output_tokens != null ? `${(u.input_tokens || 0) + (u.output_tokens || 0)} tokens` : '');
      h += `<div class="card reason bl-${m}"><div class="row"><div class="who a-${m}">${AG[m].name} <span class="mute small">${esc(f.model_name || '')}</span></div>
        <span class="act ${f.status === 'error' ? 'error' : ''}">${esc(f.status)}</span></div>
        <div class="small mute">${f.status === 'error' ? esc(f.error) : `keep ${k} · cut ${c}`} · ${esc(f.duration_s)}s ${usage ? '· ' + esc(usage) : ''}${v2 ? ' · revised in round 2' : ''}</div>
        ${f.overall_notes ? `<div style="margin-top:6px">${esc(f.overall_notes)}</div>` : ''}</div>`;
    }
    h += '</div>';
    // matrix
    h += `<div class="card" style="overflow-x:auto"><table class="matrix"><thead><tr><th>Leg</th><th>PL est.</th>${MODELS.map((m) => `<th class="a-${m}">${AG[m].name}</th>`).join('')}<th>Consensus</th><th>Result</th></tr></thead><tbody>`;
    const onSlip = new Set(run.slips ? run.slips.slips.flatMap((s) => s.legs) : []);
    for (const l of legs) {
      const c = cons[l.id]; const lr = legResult(run, l.id);
      h += `<tr><td><b>${esc(l.id)}</b> ${onSlip.has(l.id) ? '🎟️' : ''}<br>${legLabel(l)}<div class="small mute">${esc(l.sport)} · ${esc(l.event)}</div></td>
        <td class="v">${l.est_prob != null ? pct(l.est_prob) : (l.confidence != null ? esc(l.confidence) : '—')}</td>`;
      for (const m of MODELS) {
        const f = finalVotes(run, m); const v = voteFor(f, l.id); const v1 = voteFor(run.votes[m] && run.votes[m].r1, l.id);
        const changed = run.votes[m] && run.votes[m].r2 && v && v1 && v1.decision !== v.decision;
        h += `<td class="v"><span class="cell ${v ? v.decision : 'none'} ${changed ? 'changed' : ''}">${v ? (v.decision === 'abstain' ? '—' : v.decision.toUpperCase() + ' ' + v.confidence) : '·'}</span></td>`;
      }
      h += `<td class="v">${c ? `<b class="${sgn(c.score)}">${c.score > 0 ? '+' : ''}${esc(c.score)}</b><div class="small mute">${c.keep}-${c.cut}</div>` : '—'}</td><td class="v">${lr ? outcomeTag(lr.outcome) : ''}</td></tr>`;
    }
    h += '</tbody></table><div class="small dim" style="margin-top:6px">Cells show decision + confidence (0–100). Consensus = average of +conf (keep) / −conf (cut). Dashed = changed in round 2. 🎟️ = made a final slip.</div></div>';
    // reasoning
    h += '<div class="card"><h3>Reasoning by leg — tap to expand</h3>';
    for (const l of legs) {
      h += `<details><summary>${esc(l.id)} · ${legLabel(l)} ${voteDots(run, l.id)}</summary>
        <div class="reason bl-parlay-locks"><div class="who a-parlay-locks">Parlay Locks (drafter)${l.est_prob != null ? ' · est. ' + pct(l.est_prob) : ''}</div>${esc(l.reasoning)}
        ${l.key_stats && l.key_stats.length ? `<ul class="small">${l.key_stats.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>` : ''}
        ${l.injury_notes ? `<div class="small amb">Injury/news: ${esc(l.injury_notes)}</div>` : ''}
        ${l.sources && l.sources.length ? `<div class="small">${l.sources.map((s) => `<a href="${safeUrl(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>`).join(' · ')}</div>` : ''}</div>`;
      for (const m of MODELS) {
        for (const rnd of ['r1', 'r2']) {
          const doc = run.votes[m] && run.votes[m][rnd]; const v = voteFor(doc, l.id); if (!v) continue;
          h += `<div class="reason bl-${m}"><div class="who a-${m}">${AG[m].name}${rnd === 'r2' ? ' · round 2' : ''} → <span class="cell ${v.decision}">${esc(v.decision.toUpperCase())} ${esc(v.confidence)}</span></div>${esc(v.reasoning)}
            ${v.risk_flags && v.risk_flags.length ? `<div class="flags">${v.risk_flags.map((f) => `<span>${esc(f)}</span>`).join('')}</div>` : ''}</div>`;
        }
      }
      const lr = legResult(run, l.id);
      if (lr) h += `<div class="small" style="margin-top:6px">Result: ${outcomeTag(lr.outcome)} ${lr.actual != null ? 'actual ' + esc(lr.actual) : ''} ${esc(lr.note || '')}</div>`;
      h += '</details>';
    }
    h += '</div>';
  }
  el.innerHTML = h;
}

/* ---------- Feed tab ---------- */
function renderFeed() {
  const el = $('#tab-feed');
  const agents = ['all', 'parlay-locks', 'panel', ...MODELS, 'system'];
  let evs = [...S.feed].reverse();
  if (S.feedScope === 'date') evs = evs.filter((e) => (e.run ? e.run.slice(0, 10) === S.date : (e.ts || '').slice(0, 10) === S.date));
  if (S.feedAgent !== 'all') evs = evs.filter((e) => e.agent === S.feedAgent || e.to === S.feedAgent);
  let h = `<div class="filters">${agents.map((a) => `<button class="chip ${S.feedAgent === a ? 'on' : ''}" data-fa="${a}">${a === 'all' ? 'All agents' : esc(AG[a].name)}</button>`).join('')}
    <button class="chip ${S.feedScope === 'date' ? 'on' : ''}" data-fs="date">${fmtDate(S.date)}</button><button class="chip ${S.feedScope === 'all' ? 'on' : ''}" data-fs="all">All days</button></div>`;
  h += `<div class="card small"><b>Who does what:</b> ${Object.entries(AG).filter(([k]) => k !== 'system').map(([k, a]) => `${agentSpan(k)} <span class="mute">${esc(a.role)}</span>`).join(' · ')}</div>`;
  if (!evs.length) { el.innerHTML = h + empty('No agent activity here yet', 'Every draft, handoff, vote, consensus, slip and grade is logged to data/feed.jsonl and appears here as a timeline.'); return; }
  h += '<div class="feed">';
  for (const e of evs) {
    const col = (AG[e.agent] || AG.system).c;
    h += `<div class="ev" style="--av:var(${col})"><div class="meta">${agentSpan(e.agent)}${e.to ? ` → ${AG[e.to] ? agentSpan(e.to) : esc(e.to)}` : ''}<span class="act ${e.action === 'error' ? 'error' : ''}">${esc(String(e.action || '').replace('_', ' '))}</span><span>${S.feedScope === 'all' ? fmtDT(e.ts) : fmtTime(e.ts)}</span>${e.run ? `<span class="dim">${esc(e.run)}</span>` : ''}</div>
      <div class="txt">${esc(e.text)}</div>${e.ref ? `<div class="small"><a href="${ghBlob(esc(e.ref))}" target="_blank" rel="noopener">${esc(e.ref)}</a></div>` : ''}</div>`;
  }
  el.innerHTML = h + '</div>';
}

/* ---------- Models tab ---------- */
function computeModelStats() {
  const who = ['parlay-locks', ...MODELS, 'consensus'];
  const st = Object.fromEntries(who.map((w) => [w, { n: 0, correct: 0, kept: 0, keptHit: 0, brier: 0, bins: {} }]));
  const pair = {}; MODELS.forEach((a) => MODELS.forEach((b) => (pair[a + b] = { n: 0, agree: 0 })));
  const add = (w, decision, conf, hit) => {
    const s = st[w]; const pHit = decision === 'keep' ? conf / 100 : 1 - conf / 100; const y = hit ? 1 : 0;
    s.n++; s.brier += (pHit - y) ** 2; if ((decision === 'keep') === hit) s.correct++;
    if (decision === 'keep') { s.kept++; if (hit) s.keptHit++; }
    const b = Math.min(9, Math.floor(pHit * 10)); const key = b <= 3 ? '<40' : b >= 8 ? '80+' : `${b * 10}s`;
    (s.bins[key] ||= { n: 0, p: 0, y: 0 }); s.bins[key].n++; s.bins[key].p += pHit; s.bins[key].y += y;
  };
  for (const run of Object.values(S.runs)) {
    if (!run.cand || !run.results) continue;
    for (const l of run.cand.legs || []) {
      const lr = legResult(run, l.id); if (!lr || !['hit', 'miss'].includes(lr.outcome)) continue;
      const hit = lr.outcome === 'hit';
      const p = l.est_prob != null ? l.est_prob * 100 : (l.confidence != null ? l.confidence : null);
      if (p != null) add('parlay-locks', 'keep', p, hit);
      const ds = {};
      for (const m of MODELS) { const v = voteFor(finalVotes(run, m), l.id); if (v && v.decision !== 'abstain') { add(m, v.decision, v.confidence, hit); ds[m] = v.decision; } }
      const c = run.panel && (run.panel.consensus || []).find((x) => x.leg_id === l.id);
      if (c && (c.keep + c.cut)) add('consensus', c.score >= 0 ? 'keep' : 'cut', Math.min(100, 50 + Math.abs(c.score) / 2), hit);
      MODELS.forEach((a) => MODELS.forEach((b) => { if (a < b && ds[a] && ds[b]) { pair[a + b].n++; if (ds[a] === ds[b]) pair[a + b].agree++; } }));
    }
  }
  return { st, pair, who };
}
function renderModels() {
  const el = $('#tab-models'); const { st, pair, who } = computeModelStats();
  const nm = (w) => w === 'consensus' ? '<b>Panel consensus</b>' : agentSpan(w);
  let h = '<h2>Who picks best</h2>';
  if (!st.claude.n && !st.codex.n && !st.cursor.n && !st['parlay-locks'].n) {
    el.innerHTML = h + empty('No graded legs yet', 'Once Parlay Locks grades a snapshot (data/results/…), each agent\'s accuracy, keep-hit rate, Brier score and calibration appear here. Every candidate leg is graded, including ones cut from slips, so cuts are scored too.');
    return;
  }
  h += `<div class="card" style="overflow-x:auto"><table><thead><tr><th>Agent</th><th>Graded votes</th><th>Right call</th><th>Kept legs that hit</th><th>Brier ↓</th></tr></thead><tbody>`;
  for (const w of who) { const s = st[w]; if (!s.n) continue;
    h += `<tr><td>${nm(w)}</td><td>${s.n}</td><td>${pct(s.correct / s.n)}<div class="bar"><i style="width:${(s.correct / s.n) * 100}%"></i></div></td><td>${s.kept ? pct(s.keptHit / s.kept) + ` <span class="mute small">(${s.keptHit}/${s.kept})</span>` : '—'}</td><td>${(s.brier / s.n).toFixed(3)}</td></tr>`; }
  h += '</tbody></table><div class="small dim" style="margin-top:6px">Right call = kept a leg that hit or cut a leg that missed. Brier score uses each vote\'s confidence as a hit probability (0 = perfect, 0.25 = coin flip). Pushes/voids excluded. Parlay Locks always "keeps" at its estimated probability.</div></div>';
  h += '<h2>Calibration</h2><div class="card" style="overflow-x:auto"><table><thead><tr><th>Agent</th>' + ['<40', '40s', '50s', '60s', '70s', '80+'].map((b) => `<th>${b}%</th>`).join('') + '</tr></thead><tbody>';
  for (const w of who) { const s = st[w]; if (!s.n) continue;
    h += `<tr><td>${nm(w)}</td>` + ['<40', '40s', '50s', '60s', '70s', '80+'].map((b) => { const x = s.bins[b]; return `<td>${x ? `${pct(x.y / x.n)}<div class="small mute">said ${pct(x.p / x.n)} · n=${x.n}</div>` : '<span class="dim">—</span>'}</td>`; }).join('') + '</tr>'; }
  h += '</tbody></table><div class="small dim" style="margin-top:6px">Columns bucket the hit probability each agent implied; cells show how often those legs actually hit. Well calibrated = actual ≈ said.</div></div>';
  h += '<h2>Agreement between panelists</h2><div class="card"><table><tbody>';
  for (const [a, b] of [['claude', 'codex'], ['claude', 'cursor'], ['codex', 'cursor']]) { const p = pair[a + b];
    h += `<tr><td>${agentSpan(a)} ↔ ${agentSpan(b)}</td><td>${p.n ? pct(p.agree / p.n) + ` <span class="mute small">of ${p.n} graded legs</span>` : '—'}</td></tr>`; }
  el.innerHTML = h + '</tbody></table></div>';
}

/* ---------- P/L tab ---------- */
function renderPL() {
  const el = $('#tab-pl'); const rows = [];
  for (const run of Object.values(S.runs).sort((a, b) => a.id.localeCompare(b.id))) {
    if (!run.results) continue;
    for (const s of run.results.slips || []) if (s.outcome !== 'pending') rows.push({ run: run.id, snap: run.snapshot, date: run.date, ...s });
  }
  let h = '<h2>Paper P/L</h2>';
  const pending = Object.values(S.runs).filter((r) => r.slips && !r.results).length;
  if (!rows.length) {
    el.innerHTML = h + `<div class="kpis"><div class="kpi"><div class="n">$${CFG.bankroll.toFixed(2)}</div><div class="l">Paper bankroll (start)</div></div><div class="kpi"><div class="n">$1</div><div class="l">Stake per slip</div></div><div class="kpi"><div class="n">6/day</div><div class="l">Slips (2·3·5 × 2 snapshots)</div></div></div>` +
      empty('No graded slips yet', pending ? `${pending} slip set(s) waiting to be graded.` : 'P/L by slip size and snapshot, plus a running bankroll chart, appear after the first slips are graded.');
    return;
  }
  const tot = rows.reduce((a, r) => a + (+r.pnl || 0), 0), staked = rows.reduce((a, r) => a + (+r.stake || 0), 0), wins = rows.filter((r) => r.outcome === 'win').length;
  h += `<div class="kpis"><div class="kpi"><div class="n ${sgn(tot)}">${money(CFG.bankroll + tot)}</div><div class="l">Paper bankroll (from $${CFG.bankroll})</div></div>
    <div class="kpi"><div class="n ${sgn(tot)}">${money(tot)}</div><div class="l">Total P/L</div></div>
    <div class="kpi"><div class="n">${staked ? pct(tot / staked) : '—'}</div><div class="l">ROI on $${staked} staked</div></div>
    <div class="kpi"><div class="n">${wins}/${rows.length}</div><div class="l">Slips cashed</div></div></div>`;
  // breakdown
  h += '<div class="card" style="overflow-x:auto"><table><thead><tr><th>Slip</th><th>Morning</th><th>Pregame</th><th>Total</th></tr></thead><tbody>';
  const cell = (f) => { const rr = rows.filter(f); if (!rr.length) return '<span class="dim">—</span>'; const p = rr.reduce((a, r) => a + (+r.pnl || 0), 0); const w = rr.filter((r) => r.outcome === 'win').length; return `<b class="${sgn(p)}">${money(p)}</b><div class="small mute">${w}/${rr.length} cashed</div>`; };
  for (const size of [2, 3, 5]) h += `<tr><td><b>${size}-leg</b></td><td>${cell((r) => r.size === size && r.snap === 'morning')}</td><td>${cell((r) => r.size === size && r.snap === 'pregame')}</td><td>${cell((r) => r.size === size)}</td></tr>`;
  h += `<tr><td><b>All</b></td><td>${cell((r) => r.snap === 'morning')}</td><td>${cell((r) => r.snap === 'pregame')}</td><td>${cell(() => true)}</td></tr></tbody></table></div>`;
  // chart
  const series = { all: { c: '#e6ebf5', f: () => true }, 2: { c: '#3ddc97', f: (r) => r.size === 2 }, 3: { c: '#ffc857', f: (r) => r.size === 3 }, 5: { c: '#ff6b6b', f: (r) => r.size === 5 } };
  const runIds = [...new Set(rows.map((r) => r.run))];
  const pts = {}; let mn = 0, mx = 0;
  for (const [k, s] of Object.entries(series)) { let acc = 0; pts[k] = [0, ...runIds.map((id) => { acc += rows.filter((r) => r.run === id && s.f(r)).reduce((a, r) => a + (+r.pnl || 0), 0); mn = Math.min(mn, acc); mx = Math.max(mx, acc); return acc; })]; }
  const W = 700, H = 220, P = 30; const xs = (i) => P + (i * (W - 2 * P)) / Math.max(1, runIds.length); const ys = (v) => H - P - ((v - mn) * (H - 2 * P)) / Math.max(0.01, mx - mn);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="none" style="max-height:260px"><line x1="${P}" x2="${W - P}" y1="${ys(0)}" y2="${ys(0)}" stroke="#243049"/><text x="2" y="${ys(mx) + 4}">${money(mx)}</text><text x="2" y="${ys(mn) + 4}">${money(mn)}</text>`;
  for (const [k, s] of Object.entries(series)) svg += `<polyline fill="none" stroke="${s.c}" stroke-width="${k === 'all' ? 3 : 1.6}" points="${pts[k].map((v, i) => `${xs(i)},${ys(v)}`).join(' ')}"/>`;
  svg += '</svg>';
  h += `<div class="card"><h3>Cumulative P/L by graded snapshot</h3>${svg}<div class="legend">${Object.entries(series).map(([k, s]) => `<span><i style="background:${s.c}"></i>${k === 'all' ? 'All slips' : k + '-leg'}</span>`).join('')}</div></div>`;
  // list
  h += '<div class="card" style="overflow-x:auto"><table><thead><tr><th>Snapshot</th><th>Slip</th><th>Result</th><th>Payout</th><th>P/L</th></tr></thead><tbody>';
  for (const r of [...rows].reverse()) h += `<tr><td>${fmtDate(r.date)} ${esc(r.snap)}</td><td>${esc(r.size)}-leg</td><td>${outcomeTag(r.outcome)}</td><td>${money(+r.payout)}</td><td class="${sgn(r.pnl)}">${money(+r.pnl)}</td></tr>`;
  el.innerHTML = h + '</tbody></table></div>';
}

/* ---------- About tab ---------- */
function renderAbout() {
  $('#tab-about').innerHTML = `<h2>How the panel works</h2>
  <div class="card"><ol>
    <li><b class="a-parlay-locks">Parlay Locks</b> (Grok Bot) researches the slate and drafts candidate legs with reasoning and sources — ~10:15 AM and ~5:15 PM ET.</li>
    <li>The <b class="a-panel">panel runner</b> on Sneed_PC hands the same legs to <b class="a-claude">Claude</b>, <b class="a-codex">Codex</b> and <b class="a-cursor">Cursor</b> at 10:30 AM / 5:30 PM ET. Each votes keep/cut independently with a confidence and a reasoning paragraph (one short prompt per model to keep usage low; optional round 2 lets them read each other's votes and revise).</li>
    <li>Parlay Locks reads the consensus and finalizes 2-, 3- and 5-leg paper slips at 11:00 AM / 6:00 PM ET.</li>
    <li>After games, every candidate leg and slip is graded so each agent's track record and calibration build up.</li>
  </ol><div class="small mute">Paper only: $20 bankroll, flat $1 per slip, Nov 12–18, 2026. Nothing here is a real wager.</div></div>
  <div class="card small">Data lives in <a href="https://github.com/${CFG.owner}/${CFG.repo}/tree/${CFG.branch}/data" target="_blank" rel="noopener">github.com/${CFG.owner}/${CFG.repo}/data</a> · schemas in <a href="https://github.com/${CFG.owner}/${CFG.repo}/tree/${CFG.branch}/schemas" target="_blank" rel="noopener">/schemas</a>.<br>
  Add to iPad home screen: Safari → Share → Add to Home Screen. The page refreshes itself every 90 seconds.
  ${S.errors.length ? `<div class="amb" style="margin-top:8px">Load warnings: ${esc(S.errors.slice(0, 5).join(' · '))}</div>` : ''}</div>`;
}

function render() {
  Object.values(S.runs).forEach(normRun);
  renderChrome(); renderSlips(); renderPanel(); renderFeed(); renderModels(); renderPL(); renderAbout();
}

/* ---------- events ---------- */
document.addEventListener('click', (e) => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.dataset.tab) { S.tab = t.dataset.tab; location.hash = S.tab; renderChrome(); window.scrollTo({ top: 0 }); }
  else if (t.dataset.date) { S.date = t.dataset.date; render(); }
  else if (t.dataset.fa) { S.feedAgent = t.dataset.fa; renderFeed(); }
  else if (t.dataset.fs) { S.feedScope = t.dataset.fs; renderFeed(); }
  else if (t.id === 'refresh') load();
});
if (location.hash.slice(1) && AG && ['slips', 'panel', 'feed', 'models', 'pl', 'about'].includes(location.hash.slice(1))) S.tab = location.hash.slice(1);
setInterval(() => { if (document.visibilityState === 'visible') load(); }, 90000);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && S.loadedAt && Date.now() - S.loadedAt > 60000) load(); });
load();
