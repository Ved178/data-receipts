// Shared helpers for both pages. Plain ES modules: no build step, nothing fetched from outside the box.

export const $ = (sel, root = document) => root.querySelector(sel);

const ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ESC[c]);
export const short = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

export function fmtDur(s) {
  if (s == null) return "-";
  s = Math.max(0, Math.round(s));
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

export function fmtClock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

/** Set innerHTML only when it changed. Polling re-renders every 1.5 s; without this, a click that
 *  straddles a re-render is lost and hover/selection flicker. Returns true when the DOM changed. */
export function render(el, html) {
  if (el._html === html) return false;
  el._html = html;
  el.innerHTML = html;
  return true;
}

async function request(url, init) {
  const res = await fetch(url, {cache: "no-store", ...init});
  const data = await res.json().catch(() => null);
  if (!res.ok || data === null) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}
export const getJSON = url => request(url);
export const postJSON = (url, body) =>
  request(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});

/** Connection light in the top bar: stale data must never look live. */
export function setConn(ok, err) {
  const el = $("#conn");
  if (!el) return;
  el.className = "conn " + (ok ? "is-ok" : "is-down");
  el.lastElementChild.textContent = ok ? "Box connected" : "Box not responding, retrying";
  el.title = ok ? "" : String(err || "");
}

/** Run fn now and then every `ms`, never overlapping, paused while the tab is hidden.
 *  `.now()` restarts the cycle immediately (after a click). */
export function poll(fn, ms) {
  let gen = 0, timer = null;
  async function tick(mine) {
    if (!document.hidden) {
      try { await fn(); setConn(true); } catch (e) { setConn(false, e); console.warn(e); }
    }
    if (mine === gen) timer = setTimeout(tick, ms, mine);
  }
  const now = () => { clearTimeout(timer); tick(++gen); };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) now(); });
  now();
  return {now};
}

/** "Latest request wins": const mine = seq.next(); ...await...; if (!seq.is(mine)) return; */
export function sequence() {
  let n = 0;
  return {next: () => ++n, is: v => v === n};
}

// Categories that count as tracking (same set as TRACKING in dashboard/app.py).
export const TRACKING = new Set(["Advertising", "Analytics", "Social", "FingerprintingInvasive",
  "FingerprintingGeneral", "Suspected", "Cryptomining", "Shield blocklist"]);

const CAT_LABEL = {
  "Advertising": "Advertising", "Analytics": "Analytics", "Social": "Social", "Content": "Content",
  "Email": "Email", "EmailAggressive": "Email (aggressive)", "Cryptomining": "Crypto mining",
  "FingerprintingGeneral": "Fingerprinting", "FingerprintingInvasive": "Fingerprinting (invasive)",
  "ConsentManagers": "Consent managers", "Anti-fraud": "Anti-fraud", "Shield blocklist": "Shield blocklist",
  "Suspected": "Caught by our detector",
};
/** Plain words for a list category; anything new: "SomeNewCategory" -> "Some new category". */
export const catLabel = c => CAT_LABEL[c] ||
  String(c).replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().replace(/^./, x => x.toUpperCase());
