// Shield story (/story): before/after slips for one app, and the whack-a-mole timeline of one session.
import {$, esc, short, fmtDur, render, getJSON, poll, sequence} from "./lib.js";
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

  // Labels on top: first block + network switches (merged when < 5 s apart), stacked so they never overlap.
  const labels = [];
  if (t.first_block) labels.push({t: t.first_block.t, text: `Shield blocks ${short(t.first_block.company, 22)}`, color: "#33d17a", dash: "3 5", w: 2.5});
  const merged = [];
  for (const sw of t.switches) {
    const m = merged[merged.length - 1];
    if (m && sw.t - m.t < 5) m.names.push(sw.company); else merged.push({t: sw.t, names: [sw.company]});
  }
  for (const m of merged) labels.push({t: m.t, text: "→ " + m.names.map(n => short(n, 22)).join(" + "), color: "#ffb020", dash: "6 5", w: 3, bold: true});

  const L = 330, W = 1500, R = 30, rowH = 36, charW = 10.5;
  const span = Math.max(t.duration_s, 10), x = sec => L + (W - L - R) * Math.min(Math.max(sec, 0), span) / span;
  labels.sort((a, b) => a.t - b.t);
  const ends = [];   // right edge of the last label on each stacking level
  for (const lb of labels) {
    const lx = x(lb.t), len = lb.text.length * charW;
    lb.anchorEnd = lx + len + 8 > W;
    const from = lb.anchorEnd ? lx - len - 8 : lx, to = lb.anchorEnd ? lx : lx + len + 8;
    let lvl = ends.findIndex(e => e < from);
    if (lvl < 0) { lvl = ends.length; ends.push(0); }
    ends[lvl] = to; lb.lvl = lvl;
  }
  const top = 34 + 26 * Math.max(ends.length, 1) + 12, H = top + rowH * cos.length + 50;
  let g = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Timeline of tracker lookups">`;
  cos.forEach((c, i) => {   // row stripes first, so guide lines and marks sit on top of them
    if (i % 2) g += `<rect x="0" y="${top + rowH * i + 2}" width="${W}" height="${rowH - 4}" fill="#1b1e25"/>`;
  });
  const step = span > 600 ? 120 : span > 240 ? 60 : 30;
  for (let sec = 0; sec <= span; sec += step) {
    const lab = sec < 60 ? sec + "s" : Math.floor(sec / 60) + "m" + (sec % 60 ? String(sec % 60).padStart(2, "0") : "");
    g += `<line x1="${x(sec)}" y1="${top - 10}" x2="${x(sec)}" y2="${H - 40}" stroke="#2a2d36" stroke-width="1.5"/>
          <text x="${x(sec)}" y="${H - 12}" text-anchor="middle" fill="#8b8f9a" font-size="18">${lab}</text>`;
  }
  for (const lb of labels) {
    const lx = x(lb.t), ly = 28 + 26 * lb.lvl;
    g += `<line x1="${lx}" y1="${ly + 6}" x2="${lx}" y2="${H - 40}" stroke="${lb.color}" stroke-width="${lb.w}" stroke-dasharray="${lb.dash}"/>
          <text x="${lb.anchorEnd ? lx - 6 : lx + 6}" y="${ly}" fill="${lb.color}" font-size="18" font-weight="${lb.bold ? 800 : 700}"
                text-anchor="${lb.anchorEnd ? "end" : "start"}">${esc(lb.text)}</text>`;
  }
  cos.forEach((c, i) => {
    const y = top + rowH * i + rowH / 2, sus = c.category === "Suspected";
    g += `<text x="${L - 18}" y="${y + 7}" text-anchor="end" fill="${sus ? "#ffb020" : "#eceae4"}" font-size="20" font-weight="${sus ? 800 : 600}">${esc(short(c.company, 26))}<title>${esc(c.company)}</title></text>`;
    const seen = new Set();   // one mark per ~6 px per status: a burst of lookups is one mark, not a blob
    for (const [sec, st, qname] of c.events) {
      const ex = x(sec), key = st + Math.round(ex / 6);
      if (seen.has(key)) continue;
      seen.add(key);
      const tip = `<title>${esc(qname)} · ${st} · ${Math.round(sec)}s</title>`;
      if (st === "blocked") g += `<path d="M${ex - 8} ${y - 8}L${ex + 8} ${y + 8}M${ex + 8} ${y - 8}L${ex - 8} ${y + 8}" stroke="#ff4d3d" stroke-width="4" stroke-linecap="round">${tip}</path>`;
      else if (st === "caught") g += `<circle cx="${ex}" cy="${y}" r="10" fill="#ffb020" stroke="#0e0f12" stroke-width="2">${tip}</circle>`;
      else g += `<circle cx="${ex}" cy="${y}" r="7" fill="#6b7080">${tip}</circle>`;
    }
  });
  render($("#tl"), g + `</svg>`);
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
