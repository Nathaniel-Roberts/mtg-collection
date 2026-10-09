import { html, useState, api, toast, fmt, icons, CardImage, SetBadge, Sheet, Spinner, Empty, useAsync, navigate } from "/static/app.js";

export default function DecksPage() {
  const decks = useAsync(() => api.get("/api/v1/decks"), []);
  const formats = useAsync(() => api.get("/api/v1/formats"), []);
  const conflicts = useAsync(() => api.get("/api/v1/decks/conflicts"), []);
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [format, setFormat] = useState("commander");
  const [showArchived, setShowArchived] = useState(false);
  const archived = useAsync(() => showArchived ? api.get("/api/v1/decks", { include_archived: true }) : Promise.resolve(null), [showArchived]);
  const create = async () => {
    try { const d = await api.post("/api/v1/decks", { name, format }); setShowNew(false); setName(""); navigate(`/decks/${d.id}`); } catch (e) { toast(e.message, "danger"); }
  };
  const ack = async (oracleId) => { try { await api.post(`/api/v1/decks/conflicts/${oracleId}/ack`); conflicts.reload(); } catch (e) { toast(e.message, "danger"); } };
  const items = (showArchived && archived.data ? archived.data.items : (decks.data ? decks.data.items : []));
  const conflictItems = conflicts.data ? conflicts.data.items : [];
  return html`<div class="stack">
    <div class="row between"><h1>Decks</h1><button class="btn primary" onClick=${() => setShowNew(true)}>${icons.plus()} New deck</button></div>
    ${conflictItems.length ? html`<div class="banner">${icons.warn()}<div style="flex:1">
      <strong>${conflictItems.length} card${conflictItems.length === 1 ? " is" : "s are"} used by more decks than you own copies of.</strong>
      <div class="list" style="margin-top:6px">${conflictItems.map((c) => html`<div class="item" style="grid-template-columns:1fr auto;min-height:40px;padding:6px 0;border-top:1px solid color-mix(in srgb, var(--warn) 25%, transparent)">
        <div><a href="#/cards/${c.card.id}">${c.card.name}</a> <span class="small muted">own ${c.owned}, decks need ${c.needed}: ${c.decks.map((d) => `${d.name} (${d.quantity})`).join(", ")}</span></div>
        <button class="btn sm" onClick=${() => ack(c.card.oracle_id)}>Acknowledge</button>
      </div>`)}</div>
      <div class="small muted" style="margin-top:6px">Acknowledged cards stay quiet until the shortfall grows.</div>
    </div></div>` : null}
    ${decks.loading ? html`<div class="row" style="justify-content:center;padding:40px"><${Spinner} /></div>` : null}
    ${decks.data && !items.length ? html`<div class="card"><${Empty}>No decks yet. Create one, or ask your assistant through MCP to build one from what you own.</${Empty}></div>` : null}
    ${items.length ? html`<div class="card list">${items.map((d) => html`<a class="item" key=${d.id} href="#/decks/${d.id}" style="grid-template-columns:1fr auto">
      <div><div class="title">${d.name}${d.archived ? html` <span class="chip">archived</span>` : null}</div><div class="sub"><span>${d.format_name}</span><span>·</span><span>${d.cards} cards</span><span>·</span><span>updated ${fmt.date(d.updated_at)}</span></div></div>
      <div class="meta"><div class="v ${d.owned_percent < 100 ? "" : ""}" style=${d.owned_percent < 100 ? "color:var(--warn)" : "color:var(--ok)"}>${fmt.pct(d.owned_percent)}</div><div>owned</div></div>
    </a>`)}</div>` : null}
    <label class="row small muted" style="gap:8px"><input type="checkbox" checked=${showArchived} onChange=${(e) => setShowArchived(e.target.checked)} /> Show archived decks</label>

    <${Sheet} open=${showNew} onClose=${() => setShowNew(false)} title="New deck">
      <div class="stack">
        <div><label class="field">Name</label><input class="input" value=${name} onInput=${(e) => setName(e.target.value)} autoFocus placeholder="Atraxa superfriends" onKeyDown=${(e) => e.key === "Enter" && create()} /></div>
        <div><label class="field">Format</label><select class="input" value=${format} onChange=${(e) => setFormat(e.target.value)}>${(formats.data || []).map((f) => html`<option value=${f.key}>${f.name}</option>`)}</select></div>
        <button class="btn primary block" disabled=${!name.trim()} onClick=${create}>Create</button>
      </div>
    </${Sheet}>
  </div>`;
}
