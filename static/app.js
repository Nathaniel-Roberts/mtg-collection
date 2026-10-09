// App core: router, API client, shared components. Pages live in /static/pages.
import { html, render, useState, useEffect, useRef, useMemo, useCallback } from "/static/vendor/htm-preact.module.js";

export { html, useState, useEffect, useRef, useMemo, useCallback };

// ---- API ---------------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(status, detail) { super(detail || `HTTP ${status}`); this.status = status; this.detail = detail; }
}

async function request(method, path, { params, body, form } = {}) {
  const url = new URL(path, location.origin);
  if (params) for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  const init = { method, headers: {} };
  if (form) init.body = form;
  else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  const res = await fetch(url, init);
  if (res.status === 401) { throw new ApiError(401, "Not signed in. Reload the page to sign in through Cloudflare Access."); }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new ApiError(res.status, (data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : text);
  return data;
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body) => request("POST", path, { body }),
  put: (path, body) => request("PUT", path, { body }),
  patch: (path, body) => request("PATCH", path, { body }),
  del: (path) => request("DELETE", path),
  upload: (path, form) => request("POST", path, { form }),
};

// ---- preferences (per device, best effort) ------------------------------------------------

export const prefs = {
  get(key, fallback) { try { const v = localStorage.getItem(`mtg:${key}`); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(`mtg:${key}`, JSON.stringify(value)); } catch { /* private mode */ } },
};

// ---- toasts ------------------------------------------------------------------------------

const toastListeners = new Set();
let toastId = 0;
export function toast(message, kind = "") {
  const t = { id: ++toastId, message, kind };
  toastListeners.forEach((fn) => fn(t));
}
function Toasts() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const fn = (t) => { setItems((xs) => [...xs, t]); setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== t.id)), 3500); };
    toastListeners.add(fn); return () => toastListeners.delete(fn);
  }, []);
  return html`<div class="toast-wrap">${items.map((t) => html`<div key=${t.id} class="toast ${t.kind}">${t.message}</div>`)}</div>`;
}

// ---- router -------------------------------------------------------------------------------

const ROUTES = [
  ["scan", /^\/scan$/],
  ["collection", /^\/collection$/],
  ["card", /^\/cards\/([^/]+)$/],
  ["decks", /^\/decks$/],
  ["deck", /^\/decks\/(\d+)$/],
  ["import", /^\/import$/],
  ["settings", /^\/settings$/],
];
function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/scan";
  const [path, qs] = raw.split("?");
  const query = Object.fromEntries(new URLSearchParams(qs || ""));
  for (const [name, re] of ROUTES) {
    const m = path.match(re);
    if (m) return { name, path, params: m.slice(1), query };
  }
  return { name: "scan", path: "/scan", params: [], query };
}
export function navigate(hash, { replace = false } = {}) {
  if (replace) history.replaceState(null, "", `#${hash}`); else location.hash = hash;
  if (replace) window.dispatchEvent(new HashChangeEvent("hashchange"));
}
export function useRoute() {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => { const fn = () => setRoute(parseHash()); window.addEventListener("hashchange", fn); return () => window.removeEventListener("hashchange", fn); }, []);
  return route;
}

// ---- formatting ---------------------------------------------------------------------------

const audFmt = new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD" });
const usdFmt = new Intl.NumberFormat("en-AU", { style: "currency", currency: "USD" });
export const fmt = {
  aud: (n) => (n === null || n === undefined) ? "—" : audFmt.format(n),
  usd: (n) => (n === null || n === undefined) ? "—" : usdFmt.format(Number(n)),
  date: (iso) => iso ? new Date(iso).toLocaleDateString("en-AU", { day: "2-digit", month: "2-digit", year: "numeric" }) : "",
  datetime: (iso) => iso ? new Date(iso).toLocaleString("en-AU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "",
  pct: (n) => `${Math.round(n)}%`,
  n: (n) => new Intl.NumberFormat("en-AU").format(n),
  ms: (n) => n === undefined ? "" : (n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`),
};
export function priceFor(card, finish = "nonfoil") {
  const p = card.prices || {};
  if (finish === "foil") return { usd: p.usd_foil, aud: p.aud_foil };
  if (finish === "etched") return { usd: p.usd_etched, aud: p.aud_etched };
  return { usd: p.usd, aud: p.aud };
}
export const CONDITIONS = [["NM", "Near mint"], ["LP", "Lightly played"], ["MP", "Moderately played"], ["HP", "Heavily played"], ["DMG", "Damaged"]];
export const FINISH_LABEL = { nonfoil: "Non-foil", foil: "Foil", etched: "Etched" };
export const COLOURS = [["W", "White"], ["U", "Blue"], ["B", "Black"], ["R", "Red"], ["G", "Green"]];

// ---- icons (inline, single stroke set) ----------------------------------------------------

const I = (d, extra = "") => html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" dangerouslySetInnerHTML=${{ __html: d + extra }}></svg>`;
export const icons = {
  camera: () => I('<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>'),
  cards: () => I('<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>'),
  deck: () => I('<rect x="3" y="7" width="13" height="14" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-2"/>'),
  more: () => I('<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>'),
  search: () => I('<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/>'),
  filter: () => I('<path d="M4 6h16M7 12h10M10 18h4"/>'),
  grid: () => I('<rect x="4" y="4" width="6" height="6"/><rect x="14" y="4" width="6" height="6"/><rect x="4" y="14" width="6" height="6"/><rect x="14" y="14" width="6" height="6"/>'),
  list: () => I('<path d="M5 7h14M5 12h14M5 17h14"/>'),
  plus: () => I('<path d="M12 5v14M5 12h14"/>'),
  minus: () => I('<path d="M5 12h14"/>'),
  x: () => I('<path d="M6 6l12 12M18 6L6 18"/>'),
  check: () => I('<path d="M5 12l5 5L20 7"/>'),
  trash: () => I('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
  flip: () => I('<path d="M4 9a8 8 0 0 1 14-3l2 2M20 15a8 8 0 0 1-14 3l-2-2M18 4v4h-4M6 20v-4h4"/>'),
  chevron: () => I('<path d="M9 6l6 6-6 6"/>'),
  back: () => I('<path d="M15 6l-6 6 6 6"/>'),
  external: () => I('<path d="M14 4h6v6M20 4l-9 9M19 14v6H4V5h6"/>'),
  settings: () => I('<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.4.9a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.4-.9a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z"/>'),
  upload: () => I('<path d="M12 16V4M6 10l6-6 6 6M4 20h16"/>'),
  bolt: () => I('<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>'),
  copy: () => I('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
  warn: () => I('<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>'),
  refresh: () => I('<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>'),
};

// ---- shared components --------------------------------------------------------------------

export function CardImage({ card, size = "small", class: cls = "", ...rest }) {
  const src = size === "normal" ? (card.image_normal || card.image_small) : (card.image_small || card.image_normal);
  if (!src) return html`<div class="${cls} thumb" ...${rest}></div>`;
  return html`<img class=${cls} src=${src} alt=${card.name} loading="lazy" decoding="async" ...${rest} />`;
}

export function SetBadge({ card }) {
  return html`<span class="setcode" title=${card.set_name || card.set_code}>${card.set_code} ${card.collector_number}</span>`;
}

export function FinishChip({ finish }) {
  if (finish === "nonfoil") return null;
  return html`<span class="chip ${finish === "foil" ? "foil" : "accent"}">${FINISH_LABEL[finish] || finish}</span>`;
}

export function Chip({ kind = "", children }) { return html`<span class="chip ${kind}">${children}</span>`; }

export function Stepper({ value, onChange, min = 0, max = 999 }) {
  return html`<div class="stepper" role="group" aria-label="Quantity">
    <button type="button" aria-label="Decrease" onClick=${() => onChange(Math.max(min, value - 1))}>−</button>
    <span class="val">${value}</span>
    <button type="button" aria-label="Increase" onClick=${() => onChange(Math.min(max, value + 1))}>+</button>
  </div>`;
}

export function Seg({ options, value, onChange }) {
  return html`<div class="seg">${options.map(([v, label]) => html`<button type="button" class=${v === value ? "active" : ""} onClick=${() => onChange(v)}>${label}</button>`)}</div>`;
}

export function Sheet({ open, onClose, title, children }) {
  useEffect(() => {
    if (!open) return;
    const fn = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", fn); return () => window.removeEventListener("keydown", fn);
  }, [open]);
  if (!open) return null;
  return html`<div class="sheet-backdrop" onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div class="sheet" role="dialog" aria-modal="true" aria-label=${title}>
      <div class="row between" style="margin-bottom:8px">
        <h2>${title}</h2>
        <button class="btn icon ghost sm" aria-label="Close" onClick=${onClose}>${icons.x()}</button>
      </div>
      ${children}
    </div>
  </div>`;
}

export function Spinner() { return html`<span class="spin" aria-label="Loading"></span>`; }
export function Empty({ children }) { return html`<div class="empty">${children}</div>`; }

export function Autocomplete({ placeholder = "Card name", onPick, autoFocus = false, clearOnPick = true }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState([]);
  const [idx, setIdx] = useState(-1);
  const [open, setOpen] = useState(false);
  const timer = useRef(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (q.trim().length < 2) { setItems([]); return; }
    timer.current = setTimeout(async () => {
      try { const r = await api.get("/api/v1/cards/autocomplete", { q }); setItems(r.items); setOpen(true); setIdx(-1); } catch { /* ignore */ }
    }, 120);
  }, [q]);
  const pick = (name) => { setOpen(false); if (clearOnPick) setQ(""); else setQ(name); onPick(name); };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(-1, i - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); if (idx >= 0 && items[idx]) pick(items[idx]); else if (items[0]) pick(items[0]); }
    else if (e.key === "Escape") setOpen(false);
  };
  return html`<div class="ac">
    <input class="input" type="search" autocomplete="off" placeholder=${placeholder} value=${q} autoFocus=${autoFocus}
      onInput=${(e) => setQ(e.target.value)} onKeyDown=${onKey} onFocus=${() => items.length && setOpen(true)} onBlur=${() => setTimeout(() => setOpen(false), 150)} />
    ${open && items.length ? html`<div class="menu">${items.map((n, i) => html`<button type="button" class=${i === idx ? "active" : ""} onMouseDown=${() => pick(n)}>${n}</button>`)}</div>` : null}
  </div>`;
}

export function Mana({ cost }) {
  if (!cost) return null;
  return html`<span class="mana">${cost.replace(/[{}]/g, (c) => (c === "{" ? "" : " ")).trim()}</span>`;
}

export function Sparkline({ points, height = 72 }) {
  // points: [{day, value}] with value possibly null
  const vals = points.map((p) => p.value).filter((v) => v !== null && v !== undefined);
  if (vals.length < 2) return html`<div class="muted small">Not enough price history yet.</div>`;
  const w = 320, h = height, pad = 4;
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const xs = points.map((p, i) => pad + (i / Math.max(1, points.length - 1)) * (w - pad * 2));
  const ys = points.map((p) => (p.value === null || p.value === undefined) ? null : h - pad - ((p.value - min) / span) * (h - pad * 2));
  let d = "", first = true;
  points.forEach((p, i) => { if (ys[i] === null) return; d += `${first ? "M" : "L"}${xs[i].toFixed(1)},${ys[i].toFixed(1)} `; first = false; });
  const area = `${d} L${xs[xs.length - 1].toFixed(1)},${h} L${xs[0].toFixed(1)},${h} Z`;
  return html`<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Price history">
    <path class="area" d=${area}></path><path d=${d}></path>
  </svg>`;
}

export function useAsync(fn, deps) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const reload = useCallback(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn().then((data) => alive && setState({ loading: false, data, error: null }))
      .catch((error) => alive && setState({ loading: false, data: null, error }));
    return () => { alive = false; };
  }, deps);
  useEffect(() => reload(), [reload]);
  return { ...state, reload };
}

// ---- shell --------------------------------------------------------------------------------

const NAV = [["/scan", "Scan", icons.camera], ["/collection", "Collection", icons.cards], ["/decks", "Decks", icons.deck], ["/settings", "More", icons.more]];

function Shell() {
  const route = useRoute();
  const [page, setPage] = useState({ name: null, Component: null });
  useEffect(() => {
    let alive = true;
    import(`/static/pages/${route.name}.js`).then((m) => alive && setPage({ name: route.name, Component: m.default })).catch((e) => { console.error(e); toast(`Could not load page: ${e.message}`, "danger"); });
    return () => { alive = false; };
  }, [route.name]);
  // Only render a page for the route it was loaded for; a stale page would see the wrong params.
  const Page = page.name === route.name ? page.Component : null;
  const active = (p) => (route.path === p || (p === "/collection" && route.name === "card") || (p === "/decks" && route.name === "deck") || (p === "/settings" && route.name === "import")) ? "active" : "";
  return html`
    <header class="topbar">
      <a class="brand" href="#/scan">${icons.bolt()} Collection</a>
      <nav>${NAV.map(([p, label]) => html`<a href="#${p}" class=${active(p)}>${label}</a>`)}<a href="#/import" class=${route.name === "import" ? "active" : ""}>Import</a></nav>
      <span class="spacer"></span>
    </header>
    <main>${Page ? html`<${Page} route=${route} key=${route.path} />` : html`<div class="row" style="padding:40px;justify-content:center"><${Spinner} /></div>`}</main>
    <nav class="tabbar" aria-label="Primary">${NAV.map(([p, label, icon]) => html`<a href="#${p}" class=${active(p)}>${icon()}<span>${label}</span></a>`)}</nav>
    <${Toasts} />`;
}

render(html`<${Shell} />`, document.getElementById("app"));
