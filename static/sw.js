// App shell only. Every API call goes to the network; card images are cached
// opportunistically so the collection scrolls without refetching Scryfall.
const VERSION = "v1";
const SHELL = ["/", "/static/styles.css", "/static/app.js", "/static/vendor/htm-preact.module.js",
  "/static/manifest.webmanifest", "/static/icons/icon.svg"];
const PAGES = ["scan", "collection", "card", "decks", "deck", "import", "settings"].map((p) => `/static/pages/${p}.js`);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(`shell-${VERSION}`).then((c) => c.addAll([...SHELL, ...PAGES])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => !k.endsWith(VERSION)).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz" || url.pathname.startsWith("/mcp")) return;
  if (url.hostname === "cards.scryfall.io" || url.hostname === "svgs.scryfall.io") {
    event.respondWith(caches.open(`images-${VERSION}`).then(async (cache) => {
      const hit = await cache.match(event.request);
      if (hit) return hit;
      const res = await fetch(event.request);
      if (res.ok) cache.put(event.request, res.clone());
      return res;
    }));
    return;
  }
  if (url.origin === self.location.origin) {
    event.respondWith(fetch(event.request).then((res) => {
      if (res.ok) caches.open(`shell-${VERSION}`).then((c) => c.put(event.request, res.clone()));
      return res;
    }).catch(() => caches.match(event.request).then((hit) => hit || caches.match("/"))));
  }
});
