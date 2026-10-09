import { html, useState, api, toast, fmt, icons, Spinner, Empty } from "/static/app.js";

const FORMATS = [["auto", "Detect automatically"], ["manabox", "ManaBox"], ["moxfield", "Moxfield"], ["deckbox", "Deckbox"], ["archidekt", "Archidekt"], ["tcgplayer", "TCGplayer app"], ["dragonshield", "Dragon Shield"], ["mtggoldfish", "MTGGoldfish"], ["generic", "Generic (Scryfall ID or set + number)"]];

export default function ImportPage() {
  const [file, setFile] = useState(null);
  const [format, setFormat] = useState("auto");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async (dryRun) => {
    if (!file) return;
    setBusy(true);
    try {
      const form = new FormData(); form.append("file", file); form.append("format", format); form.append("dry_run", dryRun ? "true" : "false");
      const r = await api.upload("/api/v1/collection/import", form);
      setPreview(r);
      if (!dryRun) { toast(`Imported ${r.applied.copies} copies across ${r.applied.entries} rows`, "ok"); }
    } catch (e) { toast(e.message, "danger"); } finally { setBusy(false); }
  };
  return html`<div class="stack">
    <h1>Import a CSV</h1>
    <div class="card pad stack">
      <div class="field-row">
        <div><label class="field">File</label><input class="input" type="file" accept=".csv,text/csv" onChange=${(e) => { setFile(e.target.files[0] || null); setPreview(null); }} /></div>
        <div><label class="field">Format</label><select class="input" value=${format} onChange=${(e) => setFormat(e.target.value)}>${FORMATS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></div>
      </div>
      <div class="row wrap"><button class="btn primary" disabled=${!file || busy} onClick=${() => run(true)}>${busy ? html`<${Spinner} />` : icons.upload()} Preview</button>
        ${preview && !preview.applied ? html`<button class="btn" disabled=${busy || !preview.resolved} onClick=${() => run(false)}>${icons.check()} Add ${preview.copies} copies</button>` : null}</div>
      <div class="small muted">Purchase price and date columns are ignored by design. Rows merge into existing entries with the same printing, finish, condition and language.</div>
    </div>
    ${preview ? html`<div class="stack">
      <div class="stats">
        <div class="card stat"><div class="k">Format</div><div class="v" style="font-size:16px">${preview.format_detected}</div></div>
        <div class="card stat"><div class="k">Rows</div><div class="v">${preview.rows}</div></div>
        <div class="card stat"><div class="k">Matched</div><div class="v" style="color:var(--ok)">${preview.resolved}</div><div class="small muted">${preview.copies} copies</div></div>
        <div class="card stat"><div class="k">Unmatched</div><div class="v" style=${preview.unresolved.length ? "color:var(--danger)" : ""}>${preview.unresolved.length}</div></div>
      </div>
      ${preview.applied ? html`<div class="banner">${icons.check()}<div>Done: ${preview.applied.copies} copies added in ${preview.applied.entries} rows, ${preview.applied.skipped} rows skipped. <a href="#/collection?sort=added">See the collection</a>.</div></div>` : null}
      ${preview.unresolved.length ? html`<div class="card"><table class="tbl"><thead><tr><th>Line</th><th>Name</th><th>Set</th><th>Number</th><th>Reason</th></tr></thead><tbody>${preview.unresolved.map((u) => html`<tr><td>${u.line}</td><td>${u.name || ""}</td><td class="setcode">${u.set_code || ""}</td><td>${u.number || ""}</td><td class="small muted">${u.reason}</td></tr>`)}</tbody></table></div>` : null}
      <div class="card"><table class="tbl"><thead><tr><th>Line</th><th>Card</th><th>Set</th><th class="r">Qty</th><th>Finish</th><th>Cond.</th><th>Lang</th><th>Matched by</th></tr></thead><tbody>${preview.preview.map((p) => html`<tr><td>${p.line}</td><td>${p.name}</td><td class="setcode">${p.set_code || ""} ${p.number || ""}</td><td class="r">${p.quantity}</td><td>${p.finish}</td><td>${p.condition}</td><td>${p.language}</td><td class="small muted">${p.matched_by}</td></tr>`)}</tbody></table>${preview.resolved > preview.preview.length ? html`<div class="small muted" style="padding:8px 10px">Showing the first ${preview.preview.length} of ${preview.resolved} matched rows.</div>` : null}</div>
    </div>` : null}
  </div>`;
}
