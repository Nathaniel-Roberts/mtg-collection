import { html, useState, useEffect, api, toast, fmt, priceFor, icons, CardImage, SetBadge, Sheet, Seg, Spinner, Empty, Autocomplete, Mana, useAsync, navigate } from "/static/app.js";

const ROLE_LABEL = { commander: "Commander", companion: "Companion", main: "Main deck", sideboard: "Sideboard", maybeboard: "Maybeboard" };
const ROLES = ["commander", "companion", "main", "sideboard", "maybeboard"];

export default function DeckPage({ route }) {
  const id = route.params[0];
  const deck = useAsync(() => api.get(`/api/v1/decks/${id}`), [id]);
  const formats = useAsync(() => api.get("/api/v1/formats"), []);
  const [role, setRole] = useState("main");
  const [showImport, setShowImport] = useState(false);
  const [showSuggest, setShowSuggest] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [text, setText] = useState("");
  const [edit, setEdit] = useState(null);
  const [suggest, setSuggest] = useState({ theme: "", loading: false, data: null });
  const d = deck.data;
  useEffect(() => { if (d && !edit) setEdit({ name: d.name, format: d.format, description: d.description || "" }); }, [d && d.id]);

  const setCards = async (cards, mode = "add") => {
    try { await api.put(`/api/v1/decks/${id}/cards`, { cards, mode }); deck.reload(); } catch (e) { toast(e.message, "danger"); }
  };
  const addByName = async (name) => {
    try {
      const r = await api.get("/api/v1/cards/search", { q: name, unique: "prints", per_page: 60, sort: "released" });
      const exact = r.items.filter((c) => c.name.toLowerCase() === name.toLowerCase());
      const items = exact.length ? exact : r.items;
      if (!items.length) { toast("No such card", "danger"); return; }
      const owned = items.find((c) => c.owned_quantity > 0) || items[0];
      await setCards([{ card_id: owned.id, quantity: 1, role }]);
      toast(`Added ${owned.name} to ${ROLE_LABEL[role].toLowerCase()}`, "ok");
    } catch (e) { toast(e.message, "danger"); }
  };
  const importText = async (replace) => {
    try { const r = await api.post(`/api/v1/decks/${id}/import`, { text, replace }); deck.reload(); setShowImport(false); setText("");
      if (r.unresolved.length) toast(`${r.unresolved.length} line${r.unresolved.length === 1 ? "" : "s"} not matched: ${r.unresolved.map((u) => u.name).slice(0, 3).join(", ")}`, "danger"); else toast("Imported", "ok");
    } catch (e) { toast(e.message, "danger"); }
  };
  const exportText = async () => {
    try { const res = await fetch(`/api/v1/decks/${id}/export`); const t = await res.text(); await navigator.clipboard.writeText(t); toast("Deck list copied", "ok"); } catch { window.open(`/api/v1/decks/${id}/export`, "_blank"); }
  };
  const save = async () => { try { await api.patch(`/api/v1/decks/${id}`, edit); setShowEdit(false); deck.reload(); } catch (e) { toast(e.message, "danger"); } };
  const archive = async (v) => { try { await api.patch(`/api/v1/decks/${id}`, { archived: v }); deck.reload(); } catch (e) { toast(e.message, "danger"); } };
  const remove = async () => { if (!confirm(`Delete "${d.name}"? This cannot be undone.`)) return; try { await api.del(`/api/v1/decks/${id}`); navigate("/decks"); } catch (e) { toast(e.message, "danger"); } };
  const runSuggest = async () => {
    setSuggest((s) => ({ ...s, loading: true }));
    try {
      const commander = d.cards.commander[0];
      const body = { format: d.format, theme: suggest.theme || null, exclude_deck_id: Number(id), limit: 40 };
      if (commander) body.commander_id = commander.id; else body.color_identity = [...new Set(Object.values(d.cards).flat().flatMap((c) => c.color_identity.split("")))].join("") || "WUBRG";
      const data = await api.post("/api/v1/decks/suggest", body);
      setSuggest((s) => ({ ...s, loading: false, data }));
    } catch (e) { toast(e.message, "danger"); setSuggest((s) => ({ ...s, loading: false })); }
  };

  if (deck.error) return html`<${Empty}>${deck.error.message}</${Empty}>`;
  if (!d) return html`<div class="row" style="justify-content:center;padding:40px"><${Spinner} /></div>`;
  const v = d.validation;
  return html`<div class="stack">
    <div class="row between wrap">
      <a class="btn ghost sm" href="#/decks">${icons.back()} Decks</a>
      <div class="row wrap" style="gap:6px">
        <button class="btn sm" onClick=${() => setShowImport(true)}>Import list</button>
        <button class="btn sm" onClick=${exportText}>${icons.copy()} Copy list</button>
        <button class="btn sm" onClick=${() => { setShowSuggest(true); if (!suggest.data) runSuggest(); }}>Suggest from collection</button>
        <button class="btn sm ghost" onClick=${() => setShowEdit(true)}>Edit</button>
      </div>
    </div>
    <div>
      <h1>${d.name}${d.archived ? html` <span class="chip">archived</span>` : null}</h1>
      <div class="row wrap small muted" style="gap:8px;margin-top:4px"><span>${d.format_name}</span><span>·</span><span>${d.total} cards</span><span>·</span><span>${fmt.aud(d.value.aud)}</span>${d.description ? html`<span>·</span><span>${d.description}</span>` : null}</div>
    </div>
    <div class="stats">
      <div class="card stat"><div class="k">Legality</div><div class="v" style=${v.legal ? "color:var(--ok)" : "color:var(--danger)"}>${v.legal ? "Legal" : `${v.problems.length} problem${v.problems.length === 1 ? "" : "s"}`}</div></div>
      <div class="card stat"><div class="k">Owned</div><div class="v">${fmt.pct(v.ownership.percent)}</div><div class="small muted">${v.ownership.missing} missing${v.missing_value && Number(v.missing_value.usd) ? ` · ${fmt.aud(Number(v.missing_value.aud))} to buy` : ""}</div></div>
      <div class="card stat"><div class="k">Main</div><div class="v">${d.counts.main}</div><div class="small muted">${d.counts.commander ? `${d.counts.commander} commander` : ""}${d.counts.sideboard ? ` · ${d.counts.sideboard} side` : ""}</div></div>
    </div>
    ${v.problems.length ? html`<div class="card pad">${v.problems.map((p) => html`<div class="problem"><span class="code">${p.code}</span><span>${p.message}</span></div>`)}</div>` : null}
    ${v.ownership.also_in_decks.length ? html`<div class="small muted">Also used elsewhere: ${v.ownership.also_in_decks.map((a) => `${a.card} (${a.decks.map((x) => x.name).join(", ")})`).join("; ")}</div>` : null}

    <div class="card pad stack" style="gap:8px">
      <div class="row wrap" style="gap:8px">
        <div style="flex:1;min-width:200px"><${Autocomplete} onPick=${addByName} placeholder="Add a card by name" /></div>
        <select class="input" style="width:auto" value=${role} onChange=${(e) => setRole(e.target.value)} aria-label="Role">${ROLES.map((r) => html`<option value=${r}>${ROLE_LABEL[r]}</option>`)}</select>
      </div>
      <div class="small muted">Owned printings are preferred. Cards marked in red are not in your collection.</div>
    </div>

    <div class="card deck-cards">
      ${ROLES.filter((r) => d.cards[r].length).map((r) => html`<div class="group" key=${r}>
        <h3>${ROLE_LABEL[r]} · ${d.cards[r].reduce((n, c) => n + c.quantity, 0)}</h3>
        <div class="list">${d.cards[r].map((c) => html`<div class="item" key=${c.id + r}>
          <span class="qtyb">${c.quantity}×</span>
          <div><a href="#/cards/${c.id}" style="color:inherit"><span class="title ${c.owned_quantity < c.quantity ? "missing" : ""}">${c.name}</span></a> <${Mana} cost=${c.mana_cost} /> <span class="small muted">${c.type_line}</span>
            <div class="sub"><${SetBadge} card=${c} />${c.owned_quantity < c.quantity ? html`<span class="chip danger">own ${c.owned_quantity}</span>` : html`<span class="chip ok">owned</span>`}<span>${fmt.aud(priceFor(c).aud)}</span></div></div>
          <div class="row" style="gap:2px">
            <button class="btn icon sm ghost" aria-label="One fewer" onClick=${() => setCards([{ card_id: c.id, quantity: -1, role: r }])}>${icons.minus()}</button>
            <button class="btn icon sm ghost" aria-label="One more" onClick=${() => setCards([{ card_id: c.id, quantity: 1, role: r }])}>${icons.plus()}</button>
          </div>
        </div>`)}</div>
      </div>`)}
      ${!d.total && !d.counts.sideboard && !d.counts.maybeboard ? html`<${Empty}>Empty deck. Add cards above or import a list.</${Empty}>` : null}
    </div>

    <div class="row wrap" style="gap:8px"><button class="btn sm ghost" onClick=${() => archive(!d.archived)}>${d.archived ? "Unarchive" : "Archive"}</button><button class="btn sm ghost danger" onClick=${remove}>${icons.trash()} Delete deck</button></div>

    <${Sheet} open=${showImport} onClose=${() => setShowImport(false)} title="Import a deck list">
      <div class="stack">
        <textarea class="input" placeholder=${"Commander\n1 Atraxa, Praetors' Voice\n\nDeck\n1 Sol Ring (CMM) 410\n4 Forest"} value=${text} onInput=${(e) => setText(e.target.value)}></textarea>
        <div class="small muted">MTGA, Moxfield and Archidekt text exports work. Set code and collector number pick the exact printing.</div>
        <div class="row"><button class="btn primary" onClick=${() => importText(false)}>Add to deck</button><button class="btn" onClick=${() => importText(true)}>Replace deck</button></div>
      </div>
    </${Sheet}>
    <${Sheet} open=${showEdit} onClose=${() => setShowEdit(false)} title="Edit deck">
      ${edit ? html`<div class="stack">
        <div><label class="field">Name</label><input class="input" value=${edit.name} onInput=${(e) => setEdit({ ...edit, name: e.target.value })} /></div>
        <div><label class="field">Format</label><select class="input" value=${edit.format} onChange=${(e) => setEdit({ ...edit, format: e.target.value })}>${(formats.data || []).map((f) => html`<option value=${f.key}>${f.name}</option>`)}</select></div>
        <div><label class="field">Description</label><input class="input" value=${edit.description} onInput=${(e) => setEdit({ ...edit, description: e.target.value })} /></div>
        <button class="btn primary block" onClick=${save}>Save</button>
      </div>` : null}
    </${Sheet}>
    <${Sheet} open=${showSuggest} onClose=${() => setShowSuggest(false)} title="Suggestions from your collection">
      <div class="stack">
        <div class="row"><input class="input" placeholder="Theme, e.g. tokens, proliferate, draw" value=${suggest.theme} onInput=${(e) => setSuggest({ ...suggest, theme: e.target.value })} onKeyDown=${(e) => e.key === "Enter" && runSuggest()} /><button class="btn" onClick=${runSuggest}>${icons.refresh()}</button></div>
        ${suggest.loading ? html`<div class="row" style="justify-content:center"><${Spinner} /></div>` : null}
        ${suggest.data ? html`<div class="small muted">${suggest.data.considered} owned cards fit ${suggest.data.color_identity || "colourless"}; these are not in this deck yet.</div>
          <div class="list">${suggest.data.candidates.map((s) => html`<div class="item" style="grid-template-columns:36px 1fr auto;min-height:48px" key=${s.card.id}>
            <${CardImage} card=${s.card} class="thumb" style="width:36px;height:50px" />
            <div><div class="title">${s.card.name} <span class="chip">${s.category}</span></div><div class="sub">${s.why}</div></div>
            <button class="btn icon sm" aria-label="Add" onClick=${() => { setCards([{ card_id: s.card.id, quantity: 1, role: "main" }]); setSuggest((x) => ({ ...x, data: { ...x.data, candidates: x.data.candidates.filter((c) => c.card.id !== s.card.id) } })); }}>${icons.plus()}</button>
          </div>`)}</div>` : null}
      </div>
    </${Sheet}>
  </div>`;
}
