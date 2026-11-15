// /static/new.html: consumer page. Front end only: uses the existing APIs exactly as they are.
// Every number on screen comes from /api/state, /api/flags, /api/web or /api/compare.
import {$, esc, short, fmtDur, fmtClock, render, getJSON, postJSON, poll, TRACKING, catLabel} from "./lib.js";

let flags = [];
let lastState = null;

const row = (name, val, extra = "") =>
  `<div class="r-row"><span>${name}</span><span class="dots"></span><span>${val}</span>${extra}</div>`;

function renderHero(st) {
  const s = st.session;
  $("#app-name").textContent = s ? s.app_name : "No app yet";
  const live = s && !s.end_ts;
  $("#live").className = "live" + (live ? "" : " off");
  $("#live b").textContent = live ? "LIVE" : "LAST SESSION";
  $("#app-meta").textContent = s ? `${fmtDur(st.duration_s)} · ${st.totals.lookups} lookups${live ? "" : " · ended"}` : "Join DigIn and press Start";
  if (document.activeElement !== $("#app") && !$("#app").value && st.selected_app) $("#app").value = st.selected_app;
}

function renderSee(st) {
  const s = st.session, t = st.totals;
  let h = `<div class="r-title">DATA RECEIPT</div><div class="r-sub">DigIn · MadHacks 2026 · Let's dig in</div><div class="r-rule"></div>
    ${row("APP", `<b>${esc(s ? s.app_name : "-")}</b>`)}${row("TIME", fmtDur(st.duration_s))}${row("LOOKUPS", t.lookups)}<div class="r-rule"></div>`;
  const groups = st.receipt.filter(g => g.category !== "Suspected" && TRACKING.has(g.category));
  if (!groups.length) h += `<div class="r-note" style="text-align:center;padding:12px 0">Waiting for this app to reach out…</div>`;
  for (const g of groups) {
    h += `<div class="r-cat">${esc(catLabel(g.category))} (${g.companies.length})</div>${g.note ? `<div class="r-note">${esc(g.note)}</div>` : ""}`;
    for (const c of g.companies) h += row(esc(short(c.company, 24)), `x${c.hits}`, c.blocked ? ` <span class="r-blk">BLOCKED</span>` : "");
  }
  if ((st.background || []).length) {
    h += `<div class="r-cat r-bg">Background (other apps on the phone)</div><div class="r-note">Not this app. Not counted.</div>`;
    for (const b of st.background) h += `<div class="r-bg">${row(esc(b.label), `x${b.hits}`)}</div>`;
  }
  h += `<div class="r-rule"></div>${row("<span class='r-total'>COMPANIES</span>", `<span class='r-total'>${t.companies}</span>`)}
        ${row("<span class='r-total r-blk'>BLOCKED</span>", `<span class='r-total r-blk'>${t.companies_blocked}</span>`)}`;
  render($("#receipt"), h);
  $("#s-companies").textContent = t.companies;
  $("#s-lookups").textContent = t.lookups;
}

function renderBlock(st, cmp) {
  const on = st.shield === "on";
  const b = $("#shield");
  b.className = "shield " + (on ? "on" : "off");
  b.setAttribute("aria-pressed", on);
  b.querySelector(".lbl").textContent = on ? "SHIELD ON" : "SHIELD OFF";
  $("#s-blocked").textContent = st.totals.companies_blocked;
  // "Your app keeps working": real numbers only when this session is the app's latest Shield-ON round.
  const s = st.session, r = cmp && cmp.on;
  if (s && r && r.session_id === s.session_id && r.game_still_worked)
    render($("#works"), `<b>✓ Your app keeps working.</b> Its own servers answered ${r.game_lookups_ok} times, 0 blocked.`);
  else render($("#works"), on ? "<b>✓ Your app keeps working.</b> Shield blocks trackers, never the app's own servers."
                               : "Turn Shield on to cut the trackers off. Your app keeps working.");
}

function renderCatch(st) {
  const sus = st.receipt.find(g => g.category === "Suspected");
  const here = new Set(sus ? sus.companies.map(c => c.company) : []);
  // /api/flags already hides the model's sub-0.95 tier; detector (rules-v1) flags always show.
  const mine = flags.filter(f => here.has(f.domain) && f.status !== "dismissed").sort((a, b) => b.score - a.score);
  if (!mine.length) { render($("#caught"), `<div class="empty">Nothing hidden caught in this session yet.</div>`); return; }
  render($("#caught"), `<div class="flags">${mine.map(f => {
    const model = f.model_version !== "rules-v1";
    return `<div class="flag"><div class="flag-top"><span class="flag-dom">${esc(f.domain)}</span>
        <span class="flag-score">${Math.round(f.score * 100)}%</span></div>
      <span class="who ${model ? "model" : ""}">${model ? "model" : "detector"}</span>
      <ul>${(f.reasons || []).slice(0, 4).map(r => `<li>${esc(r)}</li>`).join("")}</ul></div>`;
  }).join("")}</div>`);
}

function renderWeb(web) {
  const edges = new Map();
  for (const e of web.edges) {
    if (!TRACKING.has(e.category)) continue;
    const k = e.app + "\u0000" + e.company;
    const m = edges.get(k) || {app: e.app, company: e.company, hits: 0};
    m.hits += e.hits; edges.set(k, m);
  }
  const list = [...edges.values()];
  if (!list.length) { render($("#web"), `<div class="empty">Record two or more apps to see this.</div>`); return; }
  const apps = [...new Set(list.map(e => e.app))];
  const reach = {};
  for (const e of list) reach[e.company] = (reach[e.company] || 0) + 1;
  const cos = Object.keys(reach).sort((a, b) => reach[b] - reach[a]).slice(0, 12);
  const W = 1000, LX = 250, RX = 640, rowH = 34, pad = 16, H = pad * 2 + rowH * Math.max(apps.length, cos.length);
  const y = (i, n) => pad + (H - pad * 2) * (i + .5) / n, max = Math.max(...list.map(e => e.hits), 1);
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Which tracking companies appear in which apps">`;
  for (const e of list) {
    const j = cos.indexOf(e.company); if (j < 0) continue;
    const a = y(apps.indexOf(e.app), apps.length), c = y(j, cos.length), mid = (LX + RX) / 2;
    s += `<path d="M${LX + 10} ${a} C ${mid} ${a}, ${mid} ${c}, ${RX - 10} ${c}" fill="none" stroke="#ff4d3d"
          stroke-opacity="${.25 + .6 * e.hits / max}" stroke-width="${1.5 + 3 * e.hits / max}"/>`;
  }
  apps.forEach((a, i) => s += `<text x="${LX}" y="${y(i, apps.length) + 7}" text-anchor="end" fill="#eceae4" font-size="22" font-weight="800">${esc(short(a, 18))}</text>`);
  cos.forEach((c, j) => s += `<circle cx="${RX}" cy="${y(j, cos.length)}" r="6" fill="#ff4d3d"/>
     <text x="${RX + 16}" y="${y(j, cos.length) + 7}" fill="#eceae4" font-size="20">${esc(short(c, 26))} · <tspan fill="#8b8f9a">${reach[c]} app${reach[c] > 1 ? "s" : ""}</tspan></text>`);
  render($("#web"), s + "</svg>");
}

function renderFeed(st) {
  render($("#feed"), st.recent.length ? st.recent.map(r =>
    `<div><span style="color:#5d616b">${fmtClock(r.ts)}</span> <span class="${r.blocked ? "blk" : r.tracker ? "trk" : ""}">${r.blocked ? "⛔ " : ""}${esc(r.qname)}</span></div>`).join("")
    : `<div>No lookups yet.</div>`);
}

async function refresh() {
  const st = await getJSON("/api/state?session=current");
  lastState = st;
  const cmp = st.session ? await getJSON(`/api/compare?app=${encodeURIComponent(st.session.app_name)}`).catch(() => null) : null;
  renderHero(st); renderSee(st); renderBlock(st, cmp); renderCatch(st); renderFeed(st);
}
async function refreshSlow() {
  const [f, web] = await Promise.all([getJSON("/api/flags"), getJSON("/api/web")]);
  flags = f;
  if (lastState) renderCatch(lastState);
  renderWeb(web);
}

const fast = poll(refresh, 2000);
poll(refreshSlow, 5000);
async function act(path, body) { try { await postJSON(path, body); } catch (e) { console.warn(e); } fast.now(); }
$("#shield").onclick = () => act("/api/shield", {state: $("#shield").classList.contains("on") ? "off" : "on"});
$("#start").onclick = () => { const app = $("#app").value.trim(); if (app) act("/api/session/start", {app}); else $("#app").focus(); };
$("#end").onclick = () => act("/api/session/end");
