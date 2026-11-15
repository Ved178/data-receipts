// Shield story (/story): before/after slips for one app, and the whack-a-mole timeline of one session.
import {$, esc, short, fmtDur, fmtTick, tickStep, baseDomain, render, getJSON, poll, sequence} from "./lib.js";
import {REVIEW} from "./review.js";

const params = new URLSearchParams(location.search);
let app = params.get("app") || "";          // "" = let the server pick (latest app with a Shield-ON session)
let sessionId = params.get("session") || ""; // "" = follow the before/after's Shield-ON session
let followCompare = !sessionId;
const cmpSeq = sequence(), tlSeq = sequence();

const dots = (label, val, cls = "") =>
  `<div class="row ${cls}"><span>${label}</span><span class="dots"></span><b>${val}</b></div>`;
const slip = (title, sub, body) =>
  `<div class="paper slip"><div class="k">${title}</div><div class="sub">${esc(sub)}</div>${body}</div>`;
const item = (pill, domain, why) =>
  `<div class="item"><span class="pill ${pill}">${pill}</span> <b>${esc(domain)}</b>${why ? ` <span class="why">${esc(why)}</span>` : ""}</div>`;

function renderCompare(c) {
  $("#ba-title").textContent = `Shield before / after${c.app ? " · " + c.app : ""}`;
  if (!c.off || !c.on) {
    render($("#ba"), `<div class="empty">Needs one Shield-OFF and one Shield-ON session of the same app${c.app ? ` (${esc(c.app)} has only ${c.on ? "Shield ON" : c.off ? "Shield OFF" : "empty sessions"})` : ""}.</div>`);
    return;
  }
  const off = c.off, on = c.on;
  const before = slip("SHIELD OFF", `${c.app} · ${fmtDur(off.duration_s)} · ${off.lookups} lookups`,
    `<div class="big red">${off.companies_reached}</div><div class="lbl">tracking companies reached the phone</div>
     <div class="rule"></div>
     ${dots("Blocked", off.companies_blocked)}
     ${dots("Game's own lookups answered", off.game_lookups_ok)}`);

  const rv = REVIEW[String(on.session_id)];   // hand review, when someone did one for this session
  const caught = on.caught.length, missed = rv ? rv.missed.length : 0;
  let body = `<div class="big green">${on.companies_blocked}</div><div class="lbl">companies blocked</div>
     <div class="rule"></div>
     ${dots("Tried anyway, not on our blocklists", rv ? caught + missed : caught + "+")}
     ${dots("● caught by our detector", `<span class="amberk">${caught}</span>`, "sub2")}
     ${on.caught.map(d => { const v = rv && rv.caught[d]; return item(v ? v[0] : "suspected", d, v && v[1]); }).join("")}
     ${dots("✗ missed", rv ? missed : "?", "sub2")}`;
  if (rv) {
    body += rv.missed.map(([d, why]) => item("missed", d, why)).join("") +
      rv.unclear.map(([d, why]) => item("unclear", d, why)).join("") +
      `<div class="item why">${rv.full ? "Misses found by reading the box's log by hand."
        : "Partly reviewed by hand; other names in this session not checked yet."}</div>`;
  } else {
    body += `<div class="item why">not reviewed by hand yet</div>`;
  }
  if (on.companies_through.length) {
    body += dots("Listed, got through", on.companies_through.length) +
      `<div class="names">${on.companies_through.map(esc).join(", ")}</div>`;
  }
  body += `<div class="rule"></div>` + (on.game_still_worked
    ? `<div class="ok">✓ Game still worked</div><div class="sub">its own servers answered ${on.game_lookups_ok} times, 0 blocked</div>`
    : `<div class="bad">✗ Shield blocked ${on.game_lookups_blocked} of the game's own lookups</div>`);
  const after = slip("SHIELD ON", `${c.app} · ${fmtDur(on.duration_s)} · ${on.lookups} lookups`, body);
  render($("#ba"), before + `<div class="arrow">→</div>` + after);
}

function renderTimeline(t) {
  if (t.error) {
    $("#tl-title").textContent = "Whack-a-mole";
    render($("#tl"), `<div class="empty">Session #${esc(sessionId)} is not available (${esc(t.error)}).</div>`);
    return;
  }
  const s = t.session;
  $("#tl-title").textContent = `Whack-a-mole · ${s.app_name} · session #${s.session_id} · Shield ${s.shield.toUpperCase()}`;
  const cos = t.companies;
  if (!cos.length) { render($("#tl"), `<div class="empty">No tracking companies in this session.</div>`); return; }
  // Two rows only: every Shield block on one line, every hidden tracker the detector caught on another.
  const blockedCos = new Set(), blocks = [], caught = [];
  for (const c of cos) for (const [sec, st, qname] of c.events) {
    if (st === "blocked") { blockedCos.add(c.company); blocks.push([sec, qname, c.company]); }
    else if (st === "caught") caught.push([sec, qname, c.company]);
  }
  const firstCaught = {};   // one label per caught company (by main name), at its first appearance
  for (const [sec, , co] of caught) { const k = baseDomain(co); if (!(k in firstCaught) || sec < firstCaught[k]) firstCaught[k] = sec; }
  const L = 330, W = 1500, R = 40, top = 40, rowH = 90, H = top + rowH * 2 + 50;
  const lastT = Math.max(0, ...blocks.map(e => e[0]), ...caught.map(e => e[0]));
  const span = Math.max(s.end_ts ? t.duration_s : Math.min(t.duration_s, lastT * 1.1 + 5), 10);
  const x = sec => L + (W - L - R) * Math.min(Math.max(sec, 0), span) / span;
  const y1 = top + rowH / 2, y2 = top + rowH * 1.5;
  let g = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Shield blocks and hidden trackers over time">`;
  g += `<rect x="0" y="${top + rowH + 2}" width="${W}" height="${rowH - 4}" fill="#1b1e25"/>`;
  const step = tickStep(span);
  for (let sec = 0; sec <= span; sec += step)
    g += `<line x1="${x(sec)}" y1="${top}" x2="${x(sec)}" y2="${H - 40}" stroke="#2a2d36" stroke-width="1.5"/>
          <text x="${x(sec)}" y="${H - 12}" text-anchor="middle" fill="#8b8f9a" font-size="18">${fmtTick(sec)}</text>`;
  g += `<text x="${L - 20}" y="${y1 - 4}" text-anchor="end" fill="#eceae4" font-size="21" font-weight="700">Known trackers blocked</text>
        <text x="${L - 20}" y="${y1 + 20}" text-anchor="end" fill="#ff4d3d" font-size="18" font-weight="700">${blockedCos.size} compan${blockedCos.size === 1 ? "y" : "ies"} blocked</text>
        <text x="${L - 20}" y="${y2 - 4}" text-anchor="end" fill="#eceae4" font-size="21" font-weight="700">Hidden trackers caught</text>
        <text x="${L - 20}" y="${y2 + 20}" text-anchor="end" fill="#ffb020" font-size="18" font-weight="700">${Object.keys(firstCaught).length} caught by our detector</text>`;
  const seen = new Set();
  for (const [sec, qname, co] of blocks) {
    const ex = x(sec), key = Math.round(ex / 7); if (seen.has(key)) continue; seen.add(key);
    g += `<path d="M${ex - 8} ${y1 - 8}L${ex + 8} ${y1 + 8}M${ex + 8} ${y1 - 8}L${ex - 8} ${y1 + 8}" stroke="#ff4d3d" stroke-width="4" stroke-linecap="round"><title>${esc(co)} · ${esc(qname)} · ${Math.round(sec)}s</title></path>`;
  }
  for (const [sec, qname, co] of caught)
    g += `<circle cx="${x(sec)}" cy="${y2}" r="10" fill="#ffb020" stroke="#0e0f12" stroke-width="2"><title>${esc(qname)} · ${Math.round(sec)}s</title></circle>`;
  // Labels above or below the dots; a label that would overlap another is skipped (hover a dot for its name).
  const ends = [-1e9, -1e9];
  for (const [name, sec] of Object.entries(firstCaught).sort((a, b) => a[1] - b[1])) {
    const text = short(name, 20), lx = x(sec), w = text.length * 9.6 + 14;
    const anchorEnd = lx + w > W, from = anchorEnd ? lx - w : lx - 6, to = anchorEnd ? lx + 6 : lx + w;
    const lvl = ends.findIndex(e => e < from); if (lvl < 0) continue;
    ends[lvl] = to;
    g += `<text x="${anchorEnd ? lx + 6 : lx - 6}" y="${lvl ? y2 + 32 : y2 - 18}" text-anchor="${anchorEnd ? "end" : "start"}" fill="#ffb020" font-size="17" font-weight="800">${esc(text)}</text>`;
  }
  render($("#tl"), g + `</svg><div style="font-size:20px;color:var(--text);margin-top:10px">Even with Shield on, the game kept trying new ad servers. Our detector caught them.</div>`);
}

/** "Where the trackers went": Shield OFF vs Shield ON as two bars on the same scale.
 *  Same numbers as the before/after card (/api/compare). */
function renderWhere(c) {
  if (!c.off || !c.on) { render($("#tl"), `<div class="empty">Needs one Shield-OFF and one Shield-ON session of the same app.</div>`); return; }
  const off = c.off.companies_reached, blocked = c.on.companies_blocked, caught = c.on.caught.length,
        through = c.on.companies_through.length, max = Math.max(off, blocked + caught + through, 1);
  const seg = (n, color, label) => n ? `<div class="wseg" style="flex:${n} 0 0;background:${color}" title="${n} ${label}"><b>${n}</b></div>` : "";
  const bar = (label, sub, inner, total) => `<div class="wrow"><div class="wlab"><b>${label}</b><span>${sub}</span></div>
      <div class="wtrack"><div class="wbar" style="width:${100 * total / max}%">${inner}</div></div></div>`;
  const chips = [...new Set(c.on.caught.map(d => baseDomain(d)))].map(d => `<span class="wchip">${esc(short(d, 22))}</span>`).join("");
  const onTotal = blocked + caught + through;
  render($("#tl"), `
    ${bar("Shield OFF", `${off} companies reached the phone`, seg(off, "#ff4d3d", "companies reached the phone"), off)}
    ${bar("Shield ON", `${onTotal} companies tried`, seg(blocked, "#33d17a", "blocked by Shield") + seg(caught, "#ffb020", "caught by our detector") + seg(through, "#6b7080", "got through"), onTotal)}
    ${caught ? `<div class="wrow"><div class="wlab"></div><div class="wtrack"><div class="wchips" style="margin-left:${100 * blocked / max}%">${chips}</div></div></div>` : ""}
    <div class="wcap">Shield blocks the known trackers. Our detector catches the hidden ones.</div>`);
}

function setSelect(sel, html, value) {
  if (render(sel, html) || sel.value !== String(value)) sel.value = String(value);
}

async function refresh() {
  const mine = cmpSeq.next();
  const [sessions, c] = await Promise.all([
    getJSON("/api/sessions"),
    getJSON("/api/compare" + (app ? `?app=${encodeURIComponent(app)}` : "")),
  ]);
  if (!cmpSeq.is(mine)) return;
  app = c.app || "";
  const apps = [...new Set([...c.apps_with_both, ...(app ? [app] : [])])];
  setSelect($("#app"), apps.map(a => `<option>${esc(a)}</option>`).join(""), app);
  renderCompare(c);
  renderWhere(c);
  return;   // the timeline is replaced by the "Where the trackers went" chart

  const withData = sessions.filter(s => s.lookups);
  if (followCompare || !withData.some(s => String(s.session_id) === sessionId)) {
    const pick = (c.on && c.on.session_id) ?? (withData[0] && withData[0].session_id);
    sessionId = pick == null ? "" : String(pick);
  }
  setSelect($("#session"), withData.map(s =>
    `<option value="${s.session_id}">#${s.session_id} ${esc(s.app_name)} · Shield ${s.shield.toUpperCase()}${s.end_ts ? "" : " (live)"}</option>`).join(""), sessionId);

  if (!sessionId) { render($("#tl"), `<div class="empty">No sessions with lookups yet.</div>`); return; }
  const tl = tlSeq.next();
  const t = await getJSON(`/api/timeline?session=${encodeURIComponent(sessionId)}`);
  if (tlSeq.is(tl)) renderTimeline(t);
}

const loop = poll(refresh, 5000);

$("#app").onchange = e => { app = e.target.value; followCompare = true; loop.now(); };
$("#session").onchange = e => { sessionId = e.target.value; followCompare = false; loop.now(); };
