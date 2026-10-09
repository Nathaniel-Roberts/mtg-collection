import { html, useState, useEffect, useRef, useCallback, api, toast, prefs, fmt, priceFor, icons, CardImage, SetBadge, Stepper, Seg, Sheet, Autocomplete, Spinner, CONDITIONS, FINISH_LABEL } from "/static/app.js";

const MAX_EDGE = 1280;

function useCamera(enabled) {
  const videoRef = useRef(null);
  const [state, setState] = useState({ ready: false, error: null, torch: false, canTorch: false });
  const trackRef = useRef(null);
  useEffect(() => {
    if (!enabled) return;
    let stream = null;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1440 } }, audio: false });
        const video = videoRef.current;
        if (!video) return;
        video.srcObject = stream;
        await video.play();
        const track = stream.getVideoTracks()[0];
        trackRef.current = track;
        const caps = track.getCapabilities ? track.getCapabilities() : {};
        setState({ ready: true, error: null, torch: false, canTorch: !!caps.torch });
      } catch (e) {
        setState({ ready: false, error: e.message || String(e), torch: false, canTorch: false });
      }
    })();
    return () => { if (stream) stream.getTracks().forEach((t) => t.stop()); };
  }, [enabled]);
  const toggleTorch = async () => {
    const track = trackRef.current; if (!track) return;
    try { await track.applyConstraints({ advanced: [{ torch: !state.torch }] }); setState((s) => ({ ...s, torch: !s.torch })); } catch { toast("Torch is not available on this camera"); }
  };
  return { videoRef, ...state, toggleTorch };
}

async function frameToBlob(video) {
  const w = video.videoWidth, h = video.videoHeight;
  const scale = Math.min(1, MAX_EDGE / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.86));
}

async function fileToBlob(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.86));
}

export default function ScanPage() {
  const [scanner, setScanner] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [chosen, setChosen] = useState(null);
  const [showCandidates, setShowCandidates] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [recent, setRecent] = useState([]);
  const [sessionCount, setSessionCount] = useState(0);
  const [defaults, setDefaults] = useState(() => prefs.get("scanDefaults", { finish: "nonfoil", condition: "NM" }));
  const [quantity, setQuantity] = useState(1);
  const [finish, setFinish] = useState(defaults.finish);
  const [condition, setCondition] = useState(defaults.condition);
  const cam = useCamera(true);
  const fileRef = useRef(null);

  const refreshStatus = useCallback(async () => {
    try { const s = await api.get("/api/v1/status"); setScanner(s.scanner); return s.scanner; } catch (e) { toast(e.message, "danger"); }
  }, []);
  useEffect(() => {
    refreshStatus().then((s) => { if (s && !s.ready && !s.loading && s.enabled) api.post("/api/v1/scanner/warm").catch(() => {}); });
    api.get("/api/v1/scan/recent", { limit: 12 }).then((r) => setRecent(r.items.filter((i) => i.card))).catch(() => {});
  }, []);
  useEffect(() => {
    if (!scanner || scanner.ready || !scanner.enabled) return;
    const t = setInterval(refreshStatus, 3000); return () => clearInterval(t);
  }, [scanner && scanner.ready, scanner && scanner.enabled]);

  const pickCandidate = (card) => {
    setChosen(card);
    if (card.finishes && !card.finishes.includes(finish)) setFinish(card.finishes[0] || "nonfoil");
  };

  const submit = async (blob) => {
    setBusy(true);
    try {
      const form = new FormData();
      form.append("image", blob, "scan.jpg");
      const r = await api.upload("/api/v1/scan", form);
      setResult(r);
      setShowCandidates(false); setShowSearch(false);
      setQuantity(1);
      const first = r.match ? r.match.card : (r.candidates[0] ? r.candidates[0].card : null);
      if (first) pickCandidate(first); else setChosen(null);
      if (!first) toast("No card recognised. Try again closer and with less glare.", "danger");
      if (r.errors && r.errors.length) toast(r.errors[0], "danger");
    } catch (e) {
      toast(e.message, "danger");
    } finally { setBusy(false); }
  };

  const capture = async () => {
    if (busy) return;
    const video = cam.videoRef.current;
    if (cam.ready && video && video.videoWidth) submit(await frameToBlob(video));
    else fileRef.current && fileRef.current.click();
  };

  const confirm = async () => {
    if (!result || !chosen) return;
    try {
      const entry = await api.post(`/api/v1/scan/${result.scan_id}/confirm`, { card_id: chosen.id, finish, condition, quantity });
      const next = { finish, condition };
      setDefaults(next); prefs.set("scanDefaults", next);
      setSessionCount((n) => n + quantity);
      setRecent((xs) => [{ id: result.scan_id, card: entry.card, outcome: "confirmed" }, ...xs].slice(0, 12));
      toast(`Added ${quantity} × ${entry.card.name}`, "ok");
      setResult(null); setChosen(null);
    } catch (e) { toast(e.message, "danger"); }
  };
  const skip = async () => {
    if (result) api.post(`/api/v1/scan/${result.scan_id}/reject`).catch(() => {});
    setResult(null); setChosen(null);
  };
  const pickByName = async (name) => {
    try {
      const r = await api.get("/api/v1/cards/search", { q: name, unique: "prints", per_page: 60, sort: "released" });
      const exact = r.items.filter((c) => c.name.toLowerCase() === name.toLowerCase());
      const items = exact.length ? exact : r.items;
      if (!items.length) { toast("No printings found", "danger"); return; }
      setResult((res) => ({ ...(res || { scan_id: null, candidates: [], ocr: {}, timings_ms: {} }), candidates: items.map((card) => ({ card, score: 0, reason: "manual search" })) }));
      pickCandidate(items[0]);
      setShowSearch(false); setShowCandidates(items.length > 1);
    } catch (e) { toast(e.message, "danger"); }
  };
  const manualConfirm = async () => {
    // A manual pick with no scan behind it goes straight to the collection.
    try {
      const entry = await api.post("/api/v1/collection", { card_id: chosen.id, finish, condition, quantity });
      setSessionCount((n) => n + quantity);
      toast(`Added ${quantity} × ${entry.card.name}`, "ok");
      setResult(null); setChosen(null);
    } catch (e) { toast(e.message, "danger"); }
  };

  useEffect(() => {
    const fn = (e) => {
      if (e.target && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      if (e.key === " ") { e.preventDefault(); capture(); }
      if (e.key === "Enter" && chosen) { e.preventDefault(); result && result.scan_id ? confirm() : manualConfirm(); }
      if (e.key === "Escape" && result) skip();
    };
    window.addEventListener("keydown", fn); return () => window.removeEventListener("keydown", fn);
  });

  const price = chosen ? priceFor(chosen, finish) : null;
  const finishes = chosen && chosen.finishes && chosen.finishes.length ? chosen.finishes : ["nonfoil", "foil"];
  const loadingModels = scanner && scanner.enabled && !scanner.ready;

  return html`<div class="scan">
    <div class="stack">
      ${loadingModels ? html`<div class="banner"><span class="spin"></span><div><strong>Scanner is loading</strong><div class="small muted">${scanner.loading ? "Loading models and the card catalogue. First run downloads about 35 MB." : (scanner.error || "Waiting to start")}</div></div></div>` : null}
      ${scanner && !scanner.enabled ? html`<div class="banner danger">${icons.warn()}<div>The scanner is disabled on this server. Use search below or the import page.</div></div>` : null}
      <div class="viewfinder">
        ${cam.error ? html`<div class="empty" style="color:#bbb">Camera unavailable (${cam.error}).<br/>Use the button below to pick a photo instead.</div>` : null}
        <video ref=${cam.videoRef} playsinline muted class=${cam.ready ? "" : "hidden"}></video>
        ${cam.ready ? html`<div class="guide"></div>` : null}
        <div class="status">
          ${busy ? html`<span class="chip"><span class="spin" style="width:12px;height:12px"></span> identifying</span>` : null}
          ${sessionCount ? html`<span class="chip">${sessionCount} added this session</span>` : null}
        </div>
      </div>
      <div class="shutter-row">
        <button class="btn icon ghost" aria-label="Pick a photo" title="Pick a photo" onClick=${() => fileRef.current && fileRef.current.click()}>${icons.upload()}</button>
        <button class="shutter" aria-label="Capture" disabled=${busy || loadingModels} onClick=${capture}><span class="dot"></span></button>
        ${cam.canTorch ? html`<button class="btn icon ghost ${cam.torch ? "primary" : ""}" aria-label="Torch" title="Torch" onClick=${cam.toggleTorch}>${icons.bolt()}</button>` : html`<span style="width:44px"></span>`}
      </div>
      <input ref=${fileRef} type="file" accept="image/*" capture="environment" class="hidden" onChange=${async (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) submit(await fileToBlob(f)); }} />
      <p class="small muted" style="text-align:center">Fill the frame with one card. <span class="kbd">Space</span> captures, <span class="kbd">Enter</span> confirms.</p>
    </div>

    <div class="stack">
      ${chosen ? html`
        <div class="card pad stack">
          <div class="match">
            <${CardImage} card=${chosen} size="normal" />
            <div class="stack" style="gap:6px">
              <div class="name">${chosen.name}</div>
              <div class="row wrap" style="gap:6px">
                <${SetBadge} card=${chosen} />
                <span class="muted small">${chosen.set_name}</span>
                <span class="chip">${chosen.rarity}</span>
              </div>
              ${result && result.scan_id ? html`<div class="row wrap" style="gap:6px">
                ${result.match && result.match.card.id === chosen.id ? html`<span class="chip ${result.match.confident ? "ok" : ""}">${result.match.confident ? "confident" : "check this"} · ${Math.round(result.match.score * 100)}%</span>` : html`<span class="chip">chosen manually</span>`}
                ${result.ocr && result.ocr.collector_number ? html`<span class="chip">read ${result.ocr.set_code ? result.ocr.set_code.toUpperCase() + " " : ""}${result.ocr.collector_number}</span>` : null}
                <span class="chip">${fmt.ms(result.timings_ms.total)}</span>
              </div>` : null}
              <div class="row" style="gap:10px">
                <span class="num" style="font-size:17px;font-weight:600">${fmt.aud(price.aud)}</span>
                <span class="muted small">${fmt.usd(price.usd)}</span>
              </div>
            </div>
          </div>
          <div class="confirm-row">
            <div class="full"><${Seg} options=${finishes.map((f) => [f, FINISH_LABEL[f] || f])} value=${finish} onChange=${setFinish} /></div>
            <select class="input" value=${condition} onChange=${(e) => setCondition(e.target.value)} aria-label="Condition">
              ${CONDITIONS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
            </select>
            <div class="row" style="justify-content:flex-end"><${Stepper} value=${quantity} min=${1} onChange=${setQuantity} /></div>
            <button class="btn primary full" style="min-height:52px;font-size:16px" onClick=${result && result.scan_id ? confirm : manualConfirm}>${icons.check()} Add ${quantity > 1 ? `${quantity} copies` : "to collection"}</button>
            <button class="btn" onClick=${() => setShowCandidates(true)}>Other printings</button>
            <button class="btn" onClick=${() => setShowSearch(true)}>${icons.search()} Not this card</button>
            <button class="btn ghost full" onClick=${skip}>Skip</button>
          </div>
        </div>` : html`
        <div class="card pad stack">
          <h2>${busy ? "Identifying…" : "Ready"}</h2>
          <p class="muted">Capture a card, confirm the match, and move to the next one. Scans keep your last finish and condition.</p>
          <button class="btn" onClick=${() => setShowSearch(true)}>${icons.search()} Add by name instead</button>
        </div>`}

      ${recent.length ? html`<div class="section">
        <div class="section-head"><h3>Recent</h3><a href="#/collection?sort=added" class="small">Collection</a></div>
        <div class="recent-strip">${recent.map((r) => html`<a href="#/cards/${r.card.id}" title=${r.card.name}><${CardImage} card=${r.card} /></a>`)}</div>
      </div>` : null}
    </div>

    <${Sheet} open=${showCandidates} onClose=${() => setShowCandidates(false)} title="Choose the printing">
      <div class="list candidates">
        ${(result ? result.candidates : []).map((c) => html`<div class="item" role="button" tabindex="0" onClick=${() => { pickCandidate(c.card); setShowCandidates(false); }}>
          <${CardImage} card=${c.card} class="thumb" />
          <div><div class="title">${c.card.name}</div><div class="sub"><${SetBadge} card=${c.card} /><span>${c.card.set_name}</span>${c.card.owned_quantity ? html`<span class="chip ok">own ${c.card.owned_quantity}</span>` : null}</div></div>
          <div class="meta">${c.score ? html`<div class="v">${Math.round(c.score * 100)}%</div>` : null}<div>${fmt.aud(priceFor(c.card).aud)}</div></div>
        </div>`)}
      </div>
    </${Sheet}>
    <${Sheet} open=${showSearch} onClose=${() => setShowSearch(false)} title="Find a card">
      <${Autocomplete} autoFocus=${true} onPick=${pickByName} placeholder="Type a card name" />
      <p class="small muted" style="margin-top:8px">Pick a name, then choose the printing from the list.</p>
    </${Sheet}>
  </div>`;
}
