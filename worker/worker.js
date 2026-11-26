// Cloudflare Worker: lets the phone page add or remove stocks in tickers.json.
// Secrets needed (Settings > Variables and Secrets): GITHUB_TOKEN, PASSCODE

const REPO = "davidallsopda-collab/options-scanner";
const ALLOWED_ORIGIN = "https://davidallsopda-collab.github.io";
const MAX_STOCKS = 12;

function reply(body, status, origin) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status: status || 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Vary": "Origin",
    },
  });
}

function fromB64(s) {
  const bin = atob(s.replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}
function toB64(s) {
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin);
}

function same(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function gh(env, path, opts, repo) {
  return fetch("https://api.github.com/repos/" + (repo || REPO) + path, {
    ...opts,
    headers: {
      Authorization: "Bearer " + env.GITHUB_TOKEN,
      Accept: "application/vnd.github+json",
      "User-Agent": "options-scanner-worker",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
  });
}

// ---------- Cloudflare's timer (GitHub's own timer is too unreliable) ----------
// Cron trigger in Cloudflare: */15 13-21 * * 1-5   (every 15 min, weekdays, UTC)
// Works out New York time itself, so summer/winter clock changes don't matter.
const PEARS_REPO = "davidallsopda-collab/pears";
const SCAN_SLOTS = [9 * 60 + 45, 12 * 60 + 30, 15 * 60 + 30];  // Call Scanner: 9:45, 12:30, 3:30pm New York
const PEARS_FROM = 9 * 60 + 25, PEARS_TO = 16 * 60 + 50;      // Pears: market hours plus one run after the close

function newYorkNow(date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "numeric", hour12: false,
  }).formatToParts(date);
  const get = (t) => parts.find((p) => p.type === t).value;
  return { day: get("weekday"), mins: (Number(get("hour")) % 24) * 60 + Number(get("minute")) };
}

function dispatch(env, repo, workflow) {
  return gh(env, "/actions/workflows/" + workflow + "/dispatches",
            { method: "POST", body: JSON.stringify({ ref: "main" }) }, repo);
}

export default {
  async scheduled(event, env, ctx) {
    const ny = newYorkNow(new Date(event.scheduledTime));
    if (ny.day === "Sat" || ny.day === "Sun") return;
    const jobs = [];
    if (ny.mins >= PEARS_FROM && ny.mins <= PEARS_TO) jobs.push(dispatch(env, PEARS_REPO, "update.yml"));
    // a dispatched scan always runs in full, so only start it in the 15 minutes after each slot
    if (SCAN_SLOTS.some((s) => ny.mins >= s && ny.mins < s + 15)) jobs.push(dispatch(env, REPO, "scan.yml"));
    ctx.waitUntil(Promise.all(jobs));
  },

  async fetch(request, env) {
    const origin = ALLOWED_ORIGIN;
    if (request.method === "OPTIONS") return reply({}, 204, origin);
    if (request.method !== "POST") return reply({ ok: false, error: "Use POST." }, 405, origin);
    if (request.headers.get("Origin") !== ALLOWED_ORIGIN) return reply({ ok: false, error: "Not allowed from here." }, 403, origin);

    let body;
    try { body = await request.json(); } catch (e) { return reply({ ok: false, error: "Bad request." }, 400, origin); }

    if (!env.PASSCODE || !env.GITHUB_TOKEN) return reply({ ok: false, error: "The worker isn't set up yet (missing secrets)." }, 500, origin);
    if (typeof body.passcode !== "string" || !same(body.passcode, env.PASSCODE)) return reply({ ok: false, error: "Wrong passcode." }, 401, origin);

    const action = body.action;
    const symbol = String(body.symbol || "").trim().toUpperCase();
    if (!/^[A-Z]{1,5}([.-][A-Z])?$/.test(symbol)) return reply({ ok: false, error: "That doesn't look like a ticker. Use letters only, like NVDA." }, 400, origin);
    if (action !== "add" && action !== "remove") return reply({ ok: false, error: "Unknown action." }, 400, origin);
    const name = String(body.name || "").trim().replace(/[^A-Za-z0-9 .&'-]/g, "").slice(0, 30) || symbol;

    const cur = await gh(env, "/contents/tickers.json?ref=main", { method: "GET" });
    if (!cur.ok) return reply({ ok: false, error: "Couldn't read the stock list from GitHub (" + cur.status + "). Check the token." }, 502, origin);
    const file = await cur.json();
    let list;
    try { list = JSON.parse(fromB64(file.content)); } catch (e) { return reply({ ok: false, error: "tickers.json is broken." }, 500, origin); }

    if (action === "add") {
      if (list.some((s) => s.symbol.toUpperCase() === symbol)) return reply({ ok: false, error: symbol + " is already on the list." }, 409, origin);
      if (list.length >= MAX_STOCKS) return reply({ ok: false, error: "The list is full (" + MAX_STOCKS + " stocks). Remove one first." }, 400, origin);
      list.push({ symbol: symbol, name: name });
    } else {
      const next = list.filter((s) => s.symbol.toUpperCase() !== symbol);
      if (next.length === list.length) return reply({ ok: false, error: symbol + " isn't on the list." }, 404, origin);
      if (!next.length) return reply({ ok: false, error: "Can't remove the last stock." }, 400, origin);
      list = next;
    }

    const text = "[\n" + list.map((s) => '  { "symbol": ' + JSON.stringify(s.symbol) + ', "name": ' + JSON.stringify(s.name) + " }").join(",\n") + "\n]\n";
    const put = await gh(env, "/contents/tickers.json", {
      method: "PUT",
      body: JSON.stringify({
        message: (action === "add" ? "Add " : "Remove ") + symbol + " (from the app)",
        content: toB64(text),
        sha: file.sha,
        branch: "main",
      }),
    });
    if (!put.ok) return reply({ ok: false, error: "GitHub wouldn't save the change (" + put.status + "). The token may need Contents: read and write." }, 502, origin);

    const run = await gh(env, "/actions/workflows/scan.yml/dispatches", { method: "POST", body: JSON.stringify({ ref: "main" }) });
    return reply({ ok: true, scanStarted: run.ok, message: (action === "add" ? "Added " : "Removed ") + symbol + "." }, 200, origin);
  },
};
