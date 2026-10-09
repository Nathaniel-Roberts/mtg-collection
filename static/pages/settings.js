import { html, useState, useEffect, api, toast, fmt, icons, Spinner, useAsync, CONDITIONS, FINISH_LABEL } from "/static/app.js";

function Run({ label, kind, status, onDone }) {
  const [busy, setBusy] = useState(false);
  const go = async () => { setBusy(true); try { await api.post("/api/v1/sync/run", { kind, force: true }); toast(`${label} started`, "ok"); setTimeout(onDone, 1500); } catch (e) { toast(e.message, "danger"); } finally { setBusy(false); } };
  const r = status && status.runs ? status.runs[kind] : null;
  return html`<div class="item" style="grid-template-columns:1fr auto">
    <div><div class="title">${label}</div><div class="sub">${r ? html`<span>${r.status}</span><span>·</span><span>${fmt.datetime(r.finished_at || r.started_at)}</span>${r.detail && r.detail.cards !== undefined ? html`<span>· ${fmt.n(r.detail.cards)} cards</span>` : null}${r.detail && r.detail.snapshots !== undefined ? html`<span>· ${fmt.n(r.detail.snapshots)} prices</span>` : null}${r.detail && r.detail.error ? html`<span class="missing">${r.detail.error}</span>` : null}` : html`<span>never run</span>`}</div></div>
    <button class="btn sm" disabled=${busy || (status && status.running)} onClick=${go}>${busy ? html`<${Spinner} />` : icons.refresh()} Run</button>
  </div>`;
}

export default function SettingsPage() {
  const status = useAsync(() => api.get("/api/v1/status"), []);
  const settings = useAsync(() => api.get("/api/v1/settings"), []);
  const [form, setForm] = useState(null);
  useEffect(() => { if (settings.data && !form) setForm({ fx_override_usd_aud: settings.data.fx_override_usd_aud || "", fx_override_eur_aud: settings.data.fx_override_eur_aud || "", default_condition: settings.data.default_condition || "NM", default_finish: settings.data.default_finish || "nonfoil" }); }, [settings.data]);
  const save = async () => {
    try {
      const body = { default_condition: form.default_condition, default_finish: form.default_finish, clear: [] };
      for (const k of ["fx_override_usd_aud", "fx_override_eur_aud"]) { if (form[k] === "") body.clear.push(k); else body[k] = Number(form[k]); }
      await api.put("/api/v1/settings", body); toast("Saved", "ok"); status.reload();
    } catch (e) { toast(e.message, "danger"); }
  };
  const s = status.data;
  return html`<div class="stack">
    <h1>Settings and status</h1>
    <div class="row wrap" style="gap:8px"><a class="btn" href="#/import">${icons.upload()} Import CSV</a><a class="btn" href="/api/v1/collection/export.csv">Export CSV</a><a class="btn" href="/api/v1/backup">Download database backup</a></div>

    <div class="section"><div class="section-head"><h2>Sync</h2>${s ? html`<span class="small muted">next daily run ${s.next_run}${s.running ? ` · running ${s.running}` : ""}</span>` : null}</div>
      <div class="card list">
        <${Run} label="Catalogue (Scryfall bulk)" kind="catalogue" status=${s} onDone=${status.reload} />
        <${Run} label="Prices and value" kind="prices" status=${s} onDone=${status.reload} />
        <${Run} label="Exchange rates" kind="fx" status=${s} onDone=${status.reload} />
      </div>
      ${s ? html`<div class="small muted" style="margin-top:6px">${fmt.n(s.catalogue.cards)} printings in ${fmt.n(s.catalogue.sets)} sets · USD→AUD ${s.fx.usd_aud || "—"}, EUR→AUD ${s.fx.eur_aud || "—"} (${s.fx.source}${s.fx.day ? `, ${s.fx.day}` : ""})${s.last_error ? html` · <span class="missing">last error: ${s.last_error}</span>` : null}</div>` : null}
    </div>

    <div class="section"><div class="section-head"><h2>Scanner</h2></div>
      <div class="card pad">${s ? html`<div class="row wrap" style="gap:6px">
        <span class="chip ${s.scanner.ready ? "ok" : (s.scanner.loading ? "accent" : "danger")}">${!s.scanner.enabled ? "disabled" : (s.scanner.ready ? "ready" : (s.scanner.loading ? "loading" : "not loaded"))}</span>
        ${s.scanner.error ? html`<span class="missing small">${s.scanner.error}</span>` : null}
        ${s.scanner.versions && s.scanner.versions.catalog_rows ? html`<span class="small muted">${fmt.n(s.scanner.versions.catalog_rows)} printings in the image catalogue (${s.scanner.versions.catalog})</span>` : null}
        ${s.scanner.enabled && !s.scanner.ready && !s.scanner.loading ? html`<button class="btn sm" onClick=${() => api.post("/api/v1/scanner/warm").then(status.reload)}>Load now</button>` : null}
      </div>` : html`<${Spinner} />`}</div>
    </div>

    <div class="section"><div class="section-head"><h2>Preferences</h2></div>
      ${form ? html`<div class="card pad stack">
        <div class="field-row">
          <div><label class="field">USD → AUD override</label><input class="input" type="number" step="0.0001" placeholder="use fetched rate" value=${form.fx_override_usd_aud} onInput=${(e) => setForm({ ...form, fx_override_usd_aud: e.target.value })} /></div>
          <div><label class="field">EUR → AUD override</label><input class="input" type="number" step="0.0001" placeholder="use fetched rate" value=${form.fx_override_eur_aud} onInput=${(e) => setForm({ ...form, fx_override_eur_aud: e.target.value })} /></div>
          <div><label class="field">Default condition</label><select class="input" value=${form.default_condition} onChange=${(e) => setForm({ ...form, default_condition: e.target.value })}>${CONDITIONS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></div>
          <div><label class="field">Default finish</label><select class="input" value=${form.default_finish} onChange=${(e) => setForm({ ...form, default_finish: e.target.value })}>${Object.entries(FINISH_LABEL).map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></div>
        </div>
        <div class="small muted">Leave an override blank to use the daily ECB reference rate from Frankfurter. The rate in effect is stored with each day's value snapshot.</div>
        <div><button class="btn primary" onClick=${save}>Save</button></div>
      </div>` : null}
    </div>

    <div class="section"><div class="section-head"><h2>About</h2></div>
      <div class="card pad small muted">
        <p>MTG Collection ${s ? `v${s.version}` : ""}. Card data and images from Scryfall under the Wizards of the Coast Fan Content Policy; prices are Scryfall's daily USD (TCGplayer), EUR (Cardmarket) and tix values. Card identification by CollectorVision (AGPL-3.0) and RapidOCR (Apache-2.0). Exchange rates from Frankfurter (ECB). Interface built with Preact and htm.</p>
        <p>Signed in via Cloudflare Access${s && s.dev_mode ? " (dev mode)" : ""}.</p>
      </div>
    </div>
  </div>`;
}
