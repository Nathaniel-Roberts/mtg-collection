import { html, useState, useEffect, useMemo, api, toast, prefs, fmt, priceFor, icons, CardImage, SetBadge, FinishChip, Sheet, Seg, Spinner, Empty, useAsync, CONDITIONS, COLOURS, navigate } from "/static/app.js";

const SORTS = [["name", "Name"], ["added", "Recently added"], ["value", "Value"], ["released", "Release date"], ["cmc", "Mana value"], ["quantity", "Quantity"]];
const RARITIES = ["common", "uncommon", "rare", "mythic", "special", "bonus"];
const TYPES = ["Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Land", "Battle"];

export default function CollectionPage({ route }) {
  const initial = route.query || {};
  const [filters, setFilters] = useState({ q: initial.q || "", set: initial.set || "", identity: initial.identity || "", type: initial.type || "", rarity: initial.rarity || "", finish: "", condition: "", tag: initial.tag || "", format: "" });
  const [sort, setSort] = useState(initial.sort || prefs.get("collectionSort", "name"));
  const [view, setView] = useState(prefs.get("collectionView", "list"));
  const [page, setPage] = useState(1);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showFilters, setShowFilters] = useState(false);
  const summary = useAsync(() => api.get("/api/v1/collection/summary", { group_by: "finish" }), []);
  const sets = useAsync(() => api.get("/api/v1/sets"), []);
  const tags = useAsync(() => api.get("/api/v1/tags"), []);
  const [q, setQ] = useState(filters.q);

  useEffect(() => { const t = setTimeout(() => setFilters((f) => ({ ...f, q })), 200); return () => clearTimeout(t); }, [q]);
  useEffect(() => { prefs.set("collectionSort", sort); prefs.set("collectionView", view); }, [sort, view]);
  useEffect(() => { setPage(1); }, [filters, sort]);
  useEffect(() => {
    let alive = true; setLoading(true);
    api.get("/api/v1/collection", { ...filters, sort, page, per_page: 60 }).then((r) => {
      if (!alive) return;
      setItems((xs) => (page === 1 ? r.items : [...xs, ...r.items])); setTotal(r.total);
    }).catch((e) => toast(e.message, "danger")).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [filters, sort, page]);

  const activeFilters = Object.entries(filters).filter(([k, v]) => k !== "q" && v).length;
  const set = (k, v) => setFilters((f) => ({ ...f, [k]: v }));
  const totals = summary.data && summary.data.totals;

  return html`<div>
    ${totals ? html`<div class="stats" style="margin-bottom:12px">
      <div class="card stat"><div class="k">Collection value</div><div class="v">${fmt.aud(totals.aud)}</div><div class="small muted">${fmt.usd(totals.usd)} · ${totals.priced_entries}/${totals.entries} rows priced</div></div>
      <div class="card stat"><div class="k">Cards</div><div class="v">${fmt.n(totals.cards)}</div><div class="small muted">${fmt.n(totals.copies)} copies</div></div>
      ${summary.data.by_finish ? html`<div class="card stat"><div class="k">Foils</div><div class="v">${fmt.n((summary.data.by_finish.find((x) => x.k === "foil") || { copies: 0 }).copies + (summary.data.by_finish.find((x) => x.k === "etched") || { copies: 0 }).copies)}</div><div class="small muted">copies</div></div>` : null}
    </div>` : null}
    <div class="toolbar">
      <div class="grow"><input class="input" type="search" placeholder="Search your collection" value=${q} onInput=${(e) => setQ(e.target.value)} /></div>
      <button class="btn ${activeFilters ? "primary" : ""}" onClick=${() => setShowFilters(true)}>${icons.filter()} Filters${activeFilters ? ` (${activeFilters})` : ""}</button>
      <select class="input" style="width:auto" value=${sort} onChange=${(e) => setSort(e.target.value)} aria-label="Sort">${SORTS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select>
      <${Seg} options=${[["list", icons.list()], ["grid", icons.grid()]]} value=${view} onChange=${setView} />
    </div>
    ${!loading && !items.length ? html`<${Empty}>${activeFilters || filters.q ? "Nothing matches those filters." : "Nothing here yet. Scan a card or import a CSV."}</${Empty}>` : null}
    ${view === "list" ? html`<div class="card list">
      ${items.map((e) => html`<a class="item" key=${e.id} href="#/cards/${e.card.id}">
        <${CardImage} card=${e.card} class="thumb" />
        <div>
          <div class="title">${e.card.name}</div>
          <div class="sub"><${SetBadge} card=${e.card} /><span>${e.card.set_name}</span><span class="chip">${e.condition}</span><${FinishChip} finish=${e.finish} />${e.language !== "en" ? html`<span class="chip">${e.language}</span>` : null}${e.tags.map((t) => html`<span class="chip accent">${t}</span>`)}</div>
        </div>
        <div class="meta"><div class="v">${e.quantity} ×</div><div>${fmt.aud(e.unit_price.aud)}</div></div>
      </a>`)}
    </div>` : html`<div class="cardgrid">
      ${items.map((e) => html`<a key=${e.id} href="#/cards/${e.card.id}"><${CardImage} card=${e.card} size="normal" /><span class="qty">${e.quantity}</span><div class="cap">${e.card.name}</div></a>`)}
    </div>`}
    <div class="pager">
      ${loading ? html`<${Spinner} />` : (items.length < total ? html`<button class="btn" onClick=${() => setPage((p) => p + 1)}>Show more (${fmt.n(total - items.length)} left)</button>` : (items.length ? html`<span class="small muted">${fmt.n(total)} rows</span>` : null))}
    </div>

    <${Sheet} open=${showFilters} onClose=${() => setShowFilters(false)} title="Filters">
      <div class="stack">
        <div class="field-row">
          <div><label class="field">Set</label><select class="input" value=${filters.set} onChange=${(e) => set("set", e.target.value)}><option value="">Any set</option>${(sets.data ? sets.data.items.filter((s) => s.owned_copies > 0) : []).map((s) => html`<option value=${s.code}>${s.name} (${s.owned_cards})</option>`)}</select></div>
          <div><label class="field">Rarity</label><select class="input" value=${filters.rarity} onChange=${(e) => set("rarity", e.target.value)}><option value="">Any</option>${RARITIES.map((r) => html`<option value=${r}>${r}</option>`)}</select></div>
          <div><label class="field">Type</label><select class="input" value=${filters.type} onChange=${(e) => set("type", e.target.value)}><option value="">Any</option>${TYPES.map((r) => html`<option value=${r}>${r}</option>`)}</select></div>
          <div><label class="field">Finish</label><select class="input" value=${filters.finish} onChange=${(e) => set("finish", e.target.value)}><option value="">Any</option><option value="nonfoil">Non-foil</option><option value="foil">Foil</option><option value="etched">Etched</option></select></div>
          <div><label class="field">Condition</label><select class="input" value=${filters.condition} onChange=${(e) => set("condition", e.target.value)}><option value="">Any</option>${CONDITIONS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></div>
          <div><label class="field">Tag</label><select class="input" value=${filters.tag} onChange=${(e) => set("tag", e.target.value)}><option value="">Any</option>${(tags.data ? tags.data.items : []).map((t) => html`<option value=${t.name}>${t.name} (${t.entries})</option>`)}</select></div>
          <div><label class="field">Legal in</label><select class="input" value=${filters.format} onChange=${(e) => set("format", e.target.value)}><option value="">Any format</option>${["standard", "pioneer", "modern", "legacy", "vintage", "pauper", "commander"].map((f) => html`<option value=${f}>${f}</option>`)}</select></div>
        </div>
        <div>
          <label class="field">Colour identity within</label>
          <div class="row wrap" style="gap:6px">${COLOURS.map(([c, name]) => html`<button type="button" class="btn sm ${filters.identity.includes(c) ? "primary" : ""}" title=${name} onClick=${() => set("identity", filters.identity.includes(c) ? filters.identity.replace(c, "") : filters.identity + c)}>${c}</button>`)}
            <button type="button" class="btn sm ghost" onClick=${() => set("identity", "")}>${filters.identity ? "clear" : "any"}</button></div>
          <div class="small muted" style="margin-top:4px">Select the colours a deck can use; colourless cards always match.</div>
        </div>
        <div class="row between"><button class="btn ghost" onClick=${() => setFilters({ q: filters.q, set: "", identity: "", type: "", rarity: "", finish: "", condition: "", tag: "", format: "" })}>Reset</button><button class="btn primary" onClick=${() => setShowFilters(false)}>Done</button></div>
      </div>
    </${Sheet}>
  </div>`;
}
