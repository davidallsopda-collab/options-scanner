// Lets the page open like an app and keep working with the last results if the signal drops.
const CACHE = "call-scanner-v3";
const SHELL = ["./", "index.html", "pair.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/icon.svg"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  var fresh = /results\.json|pairs\.json|index\.html|pair\.html|\/$/.test(new URL(req.url).pathname);
  if (fresh) {
    // newest first, fall back to the saved copy when offline
    e.respondWith(fetch(req).then(function (res) {
      var copy = res.clone(); caches.open(CACHE).then(function (c) { var u = new URL(req.url); u.search = ""; c.put(u.href, copy); }); return res;
    }).catch(function () { return caches.match(req, { ignoreSearch: true }); }));
  } else {
    e.respondWith(caches.match(req).then(function (hit) { return hit || fetch(req); }));
  }
});
