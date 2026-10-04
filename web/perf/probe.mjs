// Measures the /live dashboard in headless Chrome: load timings, main-thread long tasks, frame rate,
// network volume and failures. Run before and after every performance change and write the numbers down.
//
//   node probe.mjs [--url <live.html>] [--cpu 1|4] [--seconds 30] [--label baseline] [--out file.json]
//
// --cpu 4 slows the CPU 4x (a mid-range laptop or phone). Uses the Chrome installed on the machine.
import puppeteer from "puppeteer-core";
import { writeFileSync } from "node:fs";

const arg = (name, def) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : def; };
const URL_ = arg("url", "https://liveflights-prod-site-922120357133.s3.us-east-1.amazonaws.com/live.html");
const CPU = Number(arg("cpu", "1"));
const SECONDS = Number(arg("seconds", "30"));
const LABEL = arg("label", "run");
const OUT = arg("out", "");
const CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-first-run", "--disable-extensions"] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
const cdp = await page.createCDPSession();
await cdp.send("Network.enable");
if (CPU > 1) await cdp.send("Emulation.setCPUThrottlingRate", { rate: CPU });

// network accounting through CDP (encodedDataLength = bytes actually sent over the wire)
const reqs = new Map();
cdp.on("Network.requestWillBeSent", (e) => reqs.set(e.requestId, { url: e.request.url, t0: e.timestamp, status: null, bytes: 0, t1: null, failed: false }));
cdp.on("Network.responseReceived", (e) => { const r = reqs.get(e.requestId); if (r) { r.status = e.response.status; r.enc = e.response.headers["content-encoding"] || e.response.headers["Content-Encoding"] || ""; } });
cdp.on("Network.loadingFinished", (e) => { const r = reqs.get(e.requestId); if (r) { r.bytes = e.encodedDataLength; r.t1 = e.timestamp; } });
cdp.on("Network.loadingFailed", (e) => { const r = reqs.get(e.requestId); if (r) { r.failed = true; r.t1 = e.timestamp; } });

await page.evaluateOnNewDocument(() => {
  window.__perf = { long: [], cls: 0, lcp: 0, fcp: 0, frames: 0, firstMarkerAt: null };
  const po = (type, cb) => { try { new PerformanceObserver((l) => l.getEntries().forEach(cb)).observe({ type, buffered: true }); } catch {} };
  po("longtask", (e) => window.__perf.long.push(e.duration));
  po("layout-shift", (e) => { if (!e.hadRecentInput) window.__perf.cls += e.value; });
  po("largest-contentful-paint", (e) => { window.__perf.lcp = e.startTime; });
  po("paint", (e) => { if (e.name === "first-contentful-paint") window.__perf.fcp = e.startTime; });
  const tick = () => { window.__perf.frames++; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
});

const t0 = Date.now();
await page.goto(URL_, { waitUntil: "load", timeout: 90000 });
const loadMs = Date.now() - t0;

// wait for the aircraft to appear on the map
let markers = 0, firstMarkerMs = null;
const waitStart = Date.now();
while (Date.now() - waitStart < 60000) {
  markers = await page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length);
  if (markers > 100) { firstMarkerMs = Date.now() - t0; break; }
  await new Promise((r) => setTimeout(r, 250));
}

// steady-state window: reset counters, observe
await page.evaluate(() => { window.__perf.long.length = 0; window.__perf.frames = 0; window.__perf.windowStart = performance.now(); });
const heap0 = await page.evaluate(() => performance.memory ? performance.memory.usedJSHeapSize : 0);
await new Promise((r) => setTimeout(r, SECONDS * 1000));
const steady = await page.evaluate(() => ({
  long: window.__perf.long.slice(), frames: window.__perf.frames, seconds: (performance.now() - window.__perf.windowStart) / 1000,
  cls: window.__perf.cls, lcp: window.__perf.lcp, fcp: window.__perf.fcp,
  markers: document.querySelectorAll(".leaflet-marker-icon").length, domNodes: document.getElementsByTagName("*").length,
  heap: performance.memory ? performance.memory.usedJSHeapSize : 0,
}));
const heap1 = steady.heap;

const all = [...reqs.values()];
const api = all.filter((r) => /execute-api|\/api\//.test(r.url));
const tiles = all.filter((r) => /basemaps|tile|cartocdn|openstreetmap/.test(r.url));
const js = all.filter((r) => /\.js(\?|$)/.test(r.url));
const sum = (a) => a.reduce((s, r) => s + (r.bytes || 0), 0);
const dur = (r) => (r.t1 && r.t0 ? (r.t1 - r.t0) * 1000 : 0);
const result = {
  label: LABEL, url: URL_, cpuThrottle: CPU, observedSeconds: Number(steady.seconds.toFixed(1)),
  load: { loadEventMs: loadMs, fcpMs: Math.round(steady.fcp), lcpMs: Math.round(steady.lcp), cls: Number(steady.cls.toFixed(3)), firstAircraftMs: firstMarkerMs },
  map: { aircraftMarkers: steady.markers, domNodes: steady.domNodes },
  mainThread: {
    longTasks: steady.long.length, longTaskTotalMs: Math.round(steady.long.reduce((a, b) => a + b, 0)),
    longTaskWorstMs: Math.round(Math.max(0, ...steady.long)), fps: Number((steady.frames / steady.seconds).toFixed(1)),
    blockedPercent: Number((100 * steady.long.reduce((a, b) => a + b, 0) / (steady.seconds * 1000)).toFixed(1)),
  },
  memory: { jsHeapStartMB: Number((heap0 / 1048576).toFixed(0)), jsHeapEndMB: Number((heap1 / 1048576).toFixed(0)) },
  network: {
    requests: all.length, totalKB: Math.round(sum(all) / 1024), jsKB: Math.round(sum(js) / 1024), tilesKB: Math.round(sum(tiles) / 1024),
    apiCalls: api.length, apiKB: Math.round(sum(api) / 1024), apiAvgMs: api.length ? Math.round(api.reduce((s, r) => s + dur(r), 0) / api.length) : 0,
    apiSlowestMs: Math.round(Math.max(0, ...api.map(dur))), apiUncompressedCalls: api.filter((r) => r.status === 200 && !r.enc).length,
    failed: all.filter((r) => r.failed || (r.status && r.status >= 400)).map((r) => `${r.status || "ERR"} ${r.url.slice(0, 90)}`),
  },
};
console.log(JSON.stringify(result, null, 2));
if (OUT) writeFileSync(OUT, JSON.stringify(result, null, 2));
await browser.close();
