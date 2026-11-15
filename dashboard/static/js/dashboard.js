// The live dashboard (/): receipt, stat tiles, suspected trackers, tracker web, live lookups.
import {$, esc, short, fmtDur, fmtClock, render, getJSON, postJSON, poll, sequence, TRACKING, catLabel} from "./lib.js";

const url = new URLSearchParams(location.search);
let chosen = url.get("session") || "current";   // "current" | "all" | session id
let flags = [];             // every ml_flags row (slow poll)
let caughtHere = null;      // Set of flagged domains the selected session contacted (null = not loaded)
let live = null;            // {start_ts} of the session shown, when it is still running
let clockSkew = 0;          // server clock - browser clock, seconds
const stateSeq = sequence();

// ---------- render ----------

const row = (name, val, cls = "", extra = "") =>
  `<div class="r-row ${cls}"><span class="name">${name}</span><span class="dots"></span><span class="val">${val}</span>${extra}</div>`;

function renderReceipt(st) {
  const s = st.session, t = st.totals;
  let h = `<div class="r-center r-title">DATA RECEIPT</div>
    <div class="r-center r-small">DigIn · MadHacks 2026 · Let's dig in</div><div class="r-rule"></div>
    ${row("APP", `<b>${esc(s ? s.app_name : "All sessions")}</b>`)}
    ${row("TIME", `<span id="r-time">${fmtDur(st.duration_s)}</span>`)}
    ${row("LOOKUPS", t.lookups)}
    ${row("DOMAINS", t.domains)}
    <div class="r-rule"></div>`;
  if (!st.receipt.length) {
    h += `<div class="r-center r-small" style="padding:16px 0">Waiting for lookups...<br>Join DigIn and open an app.</div>`;
  }
  for (const g of st.receipt) {
    h += `<div class="r-cat ${g.category === "Suspected" ? "r-sus" : ""}">${esc(catLabel(g.category))} (${g.companies.length})</div>
          <div class="r-note">${esc(g.note)}</div>`;
    for (const c of g.companies) {
      h += row(esc(c.company), `x${c.hits}`, "", c.blocked ? ` <span class="r-blocked">BLOCKED</span>` : "");
    }
  }
  if ((st.background || []).length) {
    h += `<div class="r-cat r-bg">Background (other apps on the phone)</div>
          <div class="r-note">Not this app. Shown so you know, not counted below.</div>`;
    for (const b of st.background) h += row(esc(b.label), `x${b.hits}`, "r-bg");
  }
  h += `<div class="r-rule"></div>
    ${row("COMPANIES", t.companies, "r-total")}
    ${row("BLOCKED", t.companies_blocked, "r-total r-blocked")}
    <div class="r-rule"></div>
    <div class="r-center r-small">We saw who was contacted, never what was sent.<br>Shield: ${esc(st.shield.toUpperCase())} · thank you for digging in</div>`;
  // The running clock is patched in place (tickClock) so a live session does not rebuild the receipt every poll.
  const stable = h.replace(/<span id="r-time">[^<]*<\/span>/, `<span id="r-time"></span>`);
  render($("#receipt"), stable);
  $("#r-time").textContent = fmtDur(st.duration_s);
}

function renderStats(st, caught) {
  const tile = (n, label, color) =>
    `<div class="stat ${n ? color : "zero"}"><b>${n}</b><span>${label}</span></div>`;
  render($("#stats"), tile(st.totals.companies, "companies contacted", "red") +
    tile(st.totals.companies_blocked, "blocked by Shield", "green") +
    tile(caught, "caught by our detector", "amber"));
}

function renderFeed(st) {
  render($("#feed"), st.recent.length ? st.recent.map(r =>
    `<div class="feed-row ${r.blocked ? "blk" : r.tracker ? "trk" : ""}" title="${esc(r.qname)}${r.blocked ? " (blocked)" : ""}">
       <span class="t">${fmtClock(r.ts)}</span><span class="q">${esc(r.qname)}</span></div>`).join("")
    : `<div class="empty">No lookups yet.</div>`);
}

function renderSessions(st) {
  const ids = st.sessions.map(s => String(s.session_id));
  if (!["current", "all"].includes(chosen) && !ids.includes(chosen)) chosen = "current";
  const opts = [`<option value="current">Live / latest session</option>`, `<option value="all">All recent lookups</option>`]
    .concat(st.sessions.map(s =>
      `<option value="${s.session_id}">#${s.session_id} ${esc(s.app_name)}${s.end_ts ? "" : " (live)"}</option>`));
  const sel = $("#session");
  if (render(sel, opts.join("")) || sel.value !== chosen) sel.value = chosen;
}

function renderStatus(st) {
  const s = st.session, el = $("#status");
  live = s && s.end_ts == null ? s : null;
  el.className = "status" + (live ? " live" : "");
  if (!s) el.textContent = "All recent lookups";
  else if (!live) el.textContent = `#${s.session_id} ${s.app_name} · ended · ${fmtDur(st.duration_s)}`;
  tickClock();
  $("#end").disabled = !(live || st.sessions.some(x => x.end_ts == null));
}

function tickClock() {
  if (!live) return;
  const d = fmtDur(Date.now() / 1000 + clockSkew - live.start_ts);
  $("#status").textContent = `LIVE · ${live.app_name} · ${d}`;
  const t = $("#r-time");
  if (t) t.textContent = d;
}

function setShield(on) {
  $("#shield").setAttribute("aria-checked", String(on));
  $("#shield-label").textContent = on ? "Shield on" : "Shield off";
}

/** Score at or above which a flag is shown as "High confidence"; below it (down to the scorer's
 * 0.8 cutoff) it is "Possible". On fresh box sessions about 8 in 9 high-confidence flags were real. */
const HIGH_CONFIDENCE = 0.95;

function flagCard(f) {
  const pct = Math.round(f.score * 100), d = esc(f.domain);
  const acts = f.status === "auto_blocked"
    ? `<span class="tag blocked">Blocked</span><button class="small" data-flag="${d}" data-status="suspected">Unblock</button>`
    : `<button class="small" data-flag="${d}" data-status="auto_blocked">Block</button>
       <button class="small" data-flag="${d}" data-status="dismissed" title="Not a tracker: hide it and stop flagging it">Dismiss</button>`;
  return `<div class="flag ${esc(f.status)}${f.score < HIGH_CONFIDENCE && f.model_version !== "rules-v1" ? " possible" : ""}">
      <div class="flag-top"><span class="flag-domain">${d}</span><span class="flag-score">${pct}%</span></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <ul class="reasons">${(f.reasons || []).map(r => `<li>${esc(r)}</li>`).join("")}</ul>
      <div class="flag-acts">${acts}</div>
    </div>`;
}

/** Only flags caught in the SELECTED session: a flagged domain this session contacted.
 * The model's "Possible" tier (below 0.95) is filtered out by the server, so this is one list. */
function renderFlags() {
  if (caughtHere === null) return;
  const mine = flags.filter(f => caughtHere.has(f.domain) && f.status !== "dismissed")
    .sort((x, y) => y.score - x.score);
  if (!mine.length) { render($("#flags"), `<div class="empty">Nothing caught in this session yet.</div>`); return; }
  render($("#flags"), `<div class="flags">${mine.map(flagCard).join("")}</div>`);
}

/** Apps on the left, tracking companies on the right, one curve per (app, company). */
function renderWeb(web) {
  // One company can be filed under two categories in the same app (Google ads + analytics): merge them,
  // and keep only tracking categories so a CDN or the app's own content never shows up as a tracker.
  const edges = new Map();
  for (const e of web.edges) {
    if (!TRACKING.has(e.category)) continue;
    const k = e.app + "\u0000" + e.company;
    const m = edges.get(k) || {app: e.app, company: e.company, hits: 0, sus: false};
    m.hits += e.hits; m.sus = m.sus || e.category === "Suspected";
    edges.set(k, m);
  }
  const list = [...edges.values()];
  if (!list.length) { render($("#web"), `<div class="empty">No trackers seen in any app session yet.</div>`); return; }
  const apps = [...new Set(list.map(e => e.app))];
  const reach = {}, total = {}, sus = {};
  for (const e of list) {
    reach[e.company] = (reach[e.company] || 0) + 1;
    total[e.company] = (total[e.company] || 0) + e.hits;
    sus[e.company] = sus[e.company] || e.sus;
  }
  const cos = Object.keys(reach).sort((a, b) => reach[b] - reach[a] || total[b] - total[a]).slice(0, 12);
  const W = 600, LX = 150, RX = 380, rowH = 26, pad = 14;
  const H = pad * 2 + rowH * Math.max(apps.length, cos.length);
  const y = (i, n) => pad + (H - pad * 2) * (i + .5) / n;
  const max = Math.max(...list.map(e => e.hits), 1);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Which tracking companies appear in which apps">`;
  for (const e of list) {
    const j = cos.indexOf(e.company);
    if (j < 0) continue;
    const a = y(apps.indexOf(e.app), apps.length), c = y(j, cos.length), w = e.hits / max, mid = (LX + RX) / 2;
    s += `<path d="M${LX + 8} ${a} C ${mid} ${a}, ${mid} ${c}, ${RX - 8} ${c}" fill="none"
      stroke="${e.sus ? "#ffb020" : "#ff4d3d"}" stroke-opacity="${(.25 + .55 * w).toFixed(2)}" stroke-width="${(1 + 2.5 * w).toFixed(1)}"/>`;
  }
  apps.forEach((a, i) => {
    s += `<text x="${LX}" y="${y(i, apps.length) + 4.5}" text-anchor="end" fill="#eceae4" font-size="13.5" font-weight="700">${esc(short(a, 19))}<title>${esc(a)}</title></text>`;
  });
  cos.forEach((c, j) => {
    const n = reach[c], cy = y(j, cos.length);
    s += `<circle cx="${RX}" cy="${cy}" r="4" fill="${sus[c] ? "#ffb020" : "#ff4d3d"}"/>
      <text x="${RX + 10}" y="${cy + 4.5}" fill="#eceae4" font-size="12.5">${esc(short(c, 20))}<tspan fill="#8b8f9a"> · ${n} app${n > 1 ? "s" : ""}</tspan><title>${esc(c)}</title></text>`;
  });
  render($("#web"), s + `</svg>`);
}

// ---------- data ----------

async function refreshState() {
  const mine = stateSeq.next();
  const st = await getJSON(`/api/state?session=${encodeURIComponent(chosen)}`);
  if (!stateSeq.is(mine)) return;   // a newer request (session switch, button click) superseded this one
  clockSkew = st.server_ts - Date.now() / 1000;
  setShield(st.shield === "on");
  if (document.activeElement !== $("#app") && st.selected_app) $("#app").value = st.selected_app;
  renderSessions(st); renderReceipt(st); renderStatus(st); renderFeed(st);
  const sus = st.receipt.find(g => g.category === "Suspected");
  caughtHere = new Set(sus ? sus.companies.map(c => c.company) : []);
  renderStats(st, flags.filter(f => caughtHere.has(f.domain) && f.status !== "dismissed").length);
  renderFlags();
}

async function refreshSlow() {
  const [f, web] = await Promise.all([getJSON("/api/flags"), getJSON("/api/web")]);
  flags = f;
  renderFlags();
  renderWeb(web);
}

const fast = poll(refreshState, 1500);
const slow = poll(refreshSlow, 4000);
setInterval(tickClock, 1000);

/** POST, then refresh right away. Bumping the sequence first drops any response already in flight. */
async function act(path, body) {
  stateSeq.next();
  try { await postJSON(path, body); } catch (e) { console.warn(e); }
  fast.now(); slow.now();
}

function choose(v) {
  chosen = v;
  const u = new URL(location.href);
  if (v === "current") u.searchParams.delete("session"); else u.searchParams.set("session", v);
  history.replaceState(null, "", u);
}

// ---------- events ----------

$("#session").onchange = e => { choose(e.target.value); fast.now(); };

$("#new-session").onsubmit = e => {
  e.preventDefault();
  const input = $("#app"), app = input.value.trim();
  if (!app) {   // an unnamed session shows up as "unknown" everywhere; ask for the name instead
    input.classList.remove("invalid"); void input.offsetWidth; input.classList.add("invalid");
    input.focus();
    return;
  }
  choose("current");   // jump to the session we just started
  act("/api/session/start", {app});
};
$("#app").oninput = () => $("#app").classList.remove("invalid");
$("#app").onchange = () => act("/api/app", {app: $("#app").value});   // remembered for the box's Start button
$("#end").onclick = () => act("/api/session/end");

$("#shield").onclick = () => {
  const on = $("#shield").getAttribute("aria-checked") !== "true";
  setShield(on);   // flip at once; the next poll confirms
  act("/api/shield", {state: on ? "on" : "off"});
};

// One delegated listener: flag cards are re-rendered, the listener is not.
$("#flags").onclick = e => {
  const b = e.target.closest("button[data-flag]");
  if (!b) return;
  const f = flags.find(x => x.domain === b.dataset.flag);
  if (f) { f.status = b.dataset.status; renderFlags(); }
  act("/api/flag", {domain: b.dataset.flag, status: b.dataset.status});
};

$("#print").onclick = () => window.print();
