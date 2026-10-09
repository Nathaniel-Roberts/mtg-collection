import { html, useState, useEffect, api, toast, fmt, priceFor, icons, CardImage, SetBadge, FinishChip, Sheet, Seg, Stepper, Spinner, Empty, Sparkline, Mana, useAsync, CONDITIONS, FINISH_LABEL, prefs } from "/static/app.js";

function AddSheet({ card, open, onClose, onAdded }) {
  const d = prefs.get("scanDefaults", { finish: "nonfoil", condition: "NM" });
  const finishes = card.finishes && card.finishes.length ? card.finishes : ["nonfoil", "foil"];
  const [finish, setFinish] = useState(finishes.includes(d.finish) ? d.finish : finishes[0]);
  const [condition, setCondition] = useState(d.condition);
  const [quantity, setQuantity] = useState(1);
  const [tags, setTags] = useState("");
  const submit = async () => {
    try {
      const e = await api.post("/api/v1/collection", { card_id: card.id, finish, condition, quantity, tags: tags.split(",").map((t) => t.trim()).filter(Boolean) });
      toast(`Added ${quantity} × ${card.name}`, "ok"); onAdded(e); onClose();
    } catch (err) { toast(err.message, "danger"); }
  };
  return html`<${Sheet} open=${open} onClose=${onClose} title=${`Add ${card.name}`}>
    <div class="stack">
      <div class="row wrap"><${SetBadge} card=${card} /><span class="muted">${card.set_name}</span></div>
      <${Seg} options=${finishes.map((f) => [f, FINISH_LABEL[f] || f])} value=${finish} onChange=${setFinish} />
      <div class="field-row">
        <div><label class="field">Condition</label><select class="input" value=${condition} onChange=${(e) => setCondition(e.target.value)}>${CONDITIONS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></div>
        <div><label class="field">Quantity</label><${Stepper} value=${quantity} min=${1} onChange=${setQuantity} /></div>
      </div>
      <div><label class="field">Tags (comma separated)</label><input class="input" value=${tags} onInput=${(e) => setTags(e.target.value)} placeholder="binder A, trade" /></div>
      <button class="btn primary block" onClick=${submit}>${icons.check()} Add</button>
    </div>
  </${Sheet}>`;
}

function OwnedRow({ row, card, onChange }) {
  const [qty, setQty] = useState(row.quantity);
  const [tags, setTags] = useState(null);
  useEffect(() => setQty(row.quantity), [row.quantity]);
  const patch = async (body) => {
    try { const r = await api.patch(`/api/v1/collection/${row.id}`, body); onChange(); if (r.deleted) toast("Removed", "ok"); } catch (e) { toast(e.message, "danger"); setQty(row.quantity); }
  };
  let timer = null;
  const changeQty = (n) => { setQty(n); clearTimeout(timer); timer = setTimeout(() => patch({ quantity: n }), 350); };
  return html`<div class="item" style="grid-template-columns:1fr auto">
    <div>
      <div class="row wrap" style="gap:6px">
        <select class="input" style="width:auto;min-height:36px;padding:4px 28px 4px 8px" value=${row.finish} onChange=${(e) => patch({ finish: e.target.value })}>${(card.finishes.length ? card.finishes : ["nonfoil", "foil"]).map((f) => html`<option value=${f}>${FINISH_LABEL[f]}</option>`)}</select>
        <select class="input" style="width:auto;min-height:36px;padding:4px 28px 4px 8px" value=${row.condition} onChange=${(e) => patch({ condition: e.target.value })}>${CONDITIONS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select>
        <span class="chip">${row.language}</span>
        ${row.tags.map((t) => html`<span class="chip accent">${t}</span>`)}
        <button class="btn sm ghost" onClick=${() => setTags(tags === null ? row.tags.join(", ") : null)}>tags</button>
      </div>
      ${tags !== null ? html`<div class="row" style="margin-top:6px"><input class="input" value=${tags} onInput=${(e) => setTags(e.target.value)} placeholder="comma separated" /><button class="btn sm" onClick=${() => { patch({ tags: tags.split(",").map((t) => t.trim()).filter(Boolean) }); setTags(null); }}>Save</button></div>` : null}
      <div class="small muted" style="margin-top:4px">Added ${fmt.date(row.added_at)} via ${row.source}${row.notes ? ` · ${row.notes}` : ""}</div>
    </div>
    <div class="row"><${Stepper} value=${qty} min=${0} onChange=${changeQty} /><button class="btn icon ghost danger" aria-label="Remove" onClick=${() => patch({ quantity: 0 })}>${icons.trash()}</button></div>
  </div>`;
}

export default function CardPage({ route }) {
  const id = route.params[0];
  const [back, setBack] = useState(false);
  const [showAdd, setShowAdd] = useState(null);
  const [days, setDays] = useState(90);
  const card = useAsync(() => api.get(`/api/v1/cards/${id}`), [id]);
  const printings = useAsync(() => api.get(`/api/v1/cards/${id}/printings`), [id]);
  const owned = useAsync(() => api.get("/api/v1/collection", { per_page: 200, q: card.data ? card.data.name : "" }).then((r) => r.items.filter((e) => e.card.oracle_id === (card.data && card.data.oracle_id))), [card.data && card.data.oracle_id]);
  const history = useAsync(() => api.get(`/api/v1/cards/${id}/prices`, { days }), [id, days]);
  if (card.error) return html`<${Empty}>${card.error.message}</${Empty}>`;
  if (!card.data) return html`<div class="row" style="justify-content:center;padding:40px"><${Spinner} /></div>`;
  const c = card.data;
  const img = back && c.image_back_normal ? c.image_back_normal : (c.image_normal || c.image_small);
  const legal = Object.entries(c.legalities || {}).filter(([, v]) => v === "legal" || v === "restricted").map(([k]) => k);
  const points = (history.data ? history.data.items : []).map((p) => ({ day: p.day, value: p.usd ? Number(p.usd) : null }));
  const refresh = () => { owned.reload(); card.reload(); printings.reload(); };
  const mine = owned.data || [];
  const ownedTotal = mine.reduce((n, e) => n + e.quantity, 0);
  return html`<div class="stack">
    <div class="row between wrap">
      <a class="btn ghost sm" href="javascript:history.back()">${icons.back()} Back</a>
      <a class="btn ghost sm" href=${c.scryfall_uri} target="_blank" rel="noopener">Scryfall ${icons.external()}</a>
    </div>
    <div class="detail">
      <div class="art">
        ${img ? html`<img src=${img} alt=${c.name} />` : html`<div class="skeleton" style="aspect-ratio:63/88"></div>`}
        ${c.image_back_normal ? html`<button class="btn icon flip" aria-label="Flip" onClick=${() => setBack(!back)}>${icons.flip()}</button>` : null}
      </div>
      <div class="stack">
        <div>
          <h1>${c.name}</h1>
          <div class="row wrap" style="gap:6px;margin-top:4px"><${Mana} cost=${c.mana_cost} /><span class="muted">${c.type_line}</span></div>
          <div class="row wrap" style="gap:6px;margin-top:6px"><${SetBadge} card=${c} /><span class="muted small">${c.set_name} · ${fmt.date(c.released_at)}</span><span class="chip">${c.rarity}</span>${c.promo ? html`<span class="chip">promo</span>` : null}${c.lang !== "en" ? html`<span class="chip">${c.lang}</span>` : null}</div>
        </div>
        <div class="stats">
          <div class="card stat"><div class="k">Non-foil</div><div class="v">${fmt.aud(c.prices.aud)}</div><div class="small muted">${fmt.usd(c.prices.usd)}${c.prices.eur ? ` · €${c.prices.eur}` : ""}</div></div>
          ${c.finishes.includes("foil") ? html`<div class="card stat"><div class="k">Foil</div><div class="v">${fmt.aud(c.prices.aud_foil)}</div><div class="small muted">${fmt.usd(c.prices.usd_foil)}</div></div>` : null}
          ${c.finishes.includes("etched") ? html`<div class="card stat"><div class="k">Etched</div><div class="v">${fmt.aud(c.prices.aud_etched)}</div><div class="small muted">${fmt.usd(c.prices.usd_etched)}</div></div>` : null}
          <div class="card stat"><div class="k">You own</div><div class="v">${c.owned_quantity}</div><div class="small muted">${ownedTotal ? `${mine.length} row${mine.length === 1 ? "" : "s"}, any printing` : "across all printings"}</div></div>
        </div>
        <div class="row wrap"><button class="btn primary" onClick=${() => setShowAdd(c)}>${icons.plus()} Add this printing</button></div>
        ${c.oracle_text ? html`<div class="card pad"><div class="oracle">${c.oracle_text}</div>${c.power ? html`<div class="muted small" style="margin-top:6px">${c.power}/${c.toughness}</div>` : null}${c.loyalty ? html`<div class="muted small" style="margin-top:6px">Loyalty ${c.loyalty}</div>` : null}</div>` : null}
      </div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Owned copies</h2><span class="small muted">${ownedTotal} total</span></div>
      ${mine.length ? html`<div class="card list">${mine.map((row) => html`<div key=${row.id} class="stack" style="gap:0">
        <div class="row" style="padding:8px 12px 0;gap:8px"><${CardImage} card=${row.card} class="thumb" style="width:28px;height:39px" /><${SetBadge} card=${row.card} /><span class="small muted">${row.card.set_name}</span><span class="spacer" style="flex:1"></span><span class="small num">${fmt.aud(row.value.aud)}</span></div>
        <${OwnedRow} row=${row} card=${row.card} onChange=${refresh} />
      </div>`)}</div>` : html`<div class="card"><${Empty}>You don't own this card yet.</${Empty}></div>`}
    </div>

    <div class="section">
      <div class="section-head"><h2>Price history</h2><${Seg} options=${[[30, "30d"], [90, "90d"], [365, "1y"]]} value=${days} onChange=${setDays} /></div>
      <div class="card pad"><${Sparkline} points=${points} />
        ${points.length ? html`<div class="row between small muted"><span>${points[0].day}</span><span>USD non-foil, daily</span><span>${points[points.length - 1].day}</span></div>` : null}
        <div class="small muted">History is recorded daily for cards you own or have in a deck.</div>
      </div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Printings</h2><span class="small muted">${printings.data ? printings.data.total : ""}</span></div>
      <div class="card list">
        ${(printings.data ? printings.data.items : []).map((p) => html`<div class="item" key=${p.id} style="grid-template-columns:44px 1fr auto auto">
          <a href="#/cards/${p.id}"><${CardImage} card=${p} class="thumb" /></a>
          <a href="#/cards/${p.id}" style="color:inherit"><div class="title">${p.set_name}</div><div class="sub"><${SetBadge} card=${p} /><span class="chip">${p.rarity}</span>${p.lang !== "en" ? html`<span class="chip">${p.lang}</span>` : null}${!p.paper ? html`<span class="chip">digital</span>` : null}${p.owned_quantity_this_printing ? html`<span class="chip ok">own ${p.owned_quantity_this_printing}</span>` : null}</div></a>
          <div class="meta"><div class="v">${fmt.aud(p.prices.aud)}</div><div>${p.prices.aud_foil ? `foil ${fmt.aud(p.prices.aud_foil)}` : ""}</div></div>
          <button class="btn icon sm" aria-label="Add" onClick=${() => setShowAdd(p)}>${icons.plus()}</button>
        </div>`)}
      </div>
    </div>

    <div class="section"><div class="section-head"><h2>Legal in</h2></div><div class="legal">${legal.length ? legal.map((f) => html`<span class="chip ${c.legalities[f] === "restricted" ? "accent" : ""}">${f}${c.legalities[f] === "restricted" ? " (restricted)" : ""}</span>`) : html`<span class="muted small">Not legal in any constructed format.</span>`}</div></div>
    ${showAdd ? html`<${AddSheet} card=${showAdd} open=${true} onClose=${() => setShowAdd(null)} onAdded=${refresh} />` : null}
  </div>`;
}
