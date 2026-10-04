// Poll-hygiene regression check (step 04). Needs no real API speed: the live-flights call is delayed on purpose.
//   1. a slow API never gets a second request stacked on top of the first (max 1 in flight),
//   2. a hidden tab makes no live-flights requests,
//   3. returning to a stale hidden tab refreshes straight away instead of waiting for the next tick.
//   node poll-hygiene-check.mjs [--url http://localhost:3000/live.html]
import puppeteer from "puppeteer-core";
const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
const URL_ = arg("url", "http://localhost:3000/live.html");
const CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-first-run"] });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
const check = (name, ok, detail = "") => console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`);

const DELAY_MS = 20000; // slower than half the 15 s poll interval, so an unguarded poller overlaps itself
let started = 0, inFlight = 0, maxInFlight = 0;
const startTimes = [];
await page.setRequestInterception(true);
page.on("request", (req) => {
  if (!req.url().includes("/api/flights/live")) return req.continue();
  started++; inFlight++; maxInFlight = Math.max(maxInFlight, inFlight); startTimes.push(Date.now());
  setTimeout(() => { req.continue().catch(() => {}); }, DELAY_MS);
});
page.on("requestfinished", (r) => { if (r.url().includes("/api/flights/live")) inFlight = Math.max(0, inFlight - 1); });
page.on("requestfailed", (r) => { if (r.url().includes("/api/flights/live")) inFlight = Math.max(0, inFlight - 1); });

await page.goto(URL_, { waitUntil: "load", timeout: 90000 });
await new Promise((r) => setTimeout(r, 38000));
check("slow API: never more than one live-flights request in flight", maxInFlight === 1, `${started} started in 38 s, max in flight ${maxInFlight}`);

// hide the tab (document.hidden is read-only, so override it), wait out two poll ticks
const setHidden = (hidden) => page.evaluate((h) => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => h });
  document.dispatchEvent(new Event("visibilitychange"));
}, hidden);
await new Promise((r) => setTimeout(r, DELAY_MS)); // let any request already running finish
await setHidden(true);
const before = started;
await new Promise((r) => setTimeout(r, 35000));
check("hidden tab: no live-flights requests", started === before, `${started - before} requests in 35 s while hidden`);

await setHidden(false);
await new Promise((r) => setTimeout(r, 1500));
check("tab visible again: stale data refreshes immediately", started === before + 1, `${started - before} request(s) within 1.5 s`);
check("no uncaught page errors", errors.length === 0, errors.join(" | "));
await browser.close();
