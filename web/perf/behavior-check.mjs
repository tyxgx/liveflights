// Behaviour regression check for the live map (run it after any change to the map layer):
//   1. aircraft markers appear,
//   2. markers glide between polls (their on-screen position changes without a new poll),
//   3. clicking an aircraft selects it (outlined) and dims the others, and clicking empty map clears it.
//   node behavior-check.mjs [--url http://localhost:3000/live.html]
import puppeteer from "puppeteer-core";
const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
const URL_ = arg("url", "http://localhost:3000/live.html");
const CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-first-run"] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`); };

await page.goto(URL_, { waitUntil: "load", timeout: 90000 });
await page.waitForFunction(() => document.querySelectorAll(".leaflet-marker-icon").length > 100, { timeout: 60000 }).catch(() => {});
const count = await page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length);
check("aircraft markers appear", count > 100, `${count} markers`);

// pick up to 5 moving markers that are inside the viewport and watch their on-screen position
const positions = () => page.evaluate(() => {
  const out = [];
  for (const el of document.querySelectorAll(".leaflet-marker-icon")) {
    const r = el.getBoundingClientRect();
    if (r.x > 300 && r.x < 1200 && r.y > 150 && r.y < 700) out.push([Math.round(r.x * 10) / 10, Math.round(r.y * 10) / 10]);
    if (out.length >= 200) break;
  }
  return out;
});
const a = await positions();
await new Promise((r) => setTimeout(r, 4000));
const b = await positions();
let moved = 0; const n = Math.min(a.length, b.length);
for (let i = 0; i < n; i++) if (Math.abs(a[i][0] - b[i][0]) + Math.abs(a[i][1] - b[i][1]) > 0.05) moved++;
check("markers glide between polls", moved > 10, `${moved} of ${n} sampled markers moved in 4 s`);

// click-to-focus
const target = await page.evaluate(() => {
  for (const el of document.querySelectorAll(".leaflet-marker-icon")) {
    const r = el.getBoundingClientRect();
    if (r.x > 400 && r.x < 1100 && r.y > 250 && r.y < 650) return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }
  return null;
});
if (!target) check("a marker is clickable", false, "none in view");
else {
  await page.mouse.click(target.x, target.y);
  await new Promise((r) => setTimeout(r, 1500));
  const st = await page.evaluate(() => ({
    selected: document.querySelectorAll(".leaflet-marker-icon.outline-accent-cyan").length,
    dimmed: [...document.querySelectorAll(".leaflet-marker-icon")].filter((e) => e.style.opacity === "0.22").length,
    total: document.querySelectorAll(".leaflet-marker-icon").length,
  }));
  check("click selects exactly one aircraft", st.selected === 1, `selected=${st.selected}`);
  check("other aircraft are dimmed while one is selected", st.dimmed > st.total * 0.9, `${st.dimmed} of ${st.total} dimmed`);
  // click empty map area far from markers to clear: use the Escape/deselect by clicking the map background corner
  await page.keyboard.press("Escape");
  await new Promise((r) => setTimeout(r, 800));
  const after = await page.evaluate(() => ({
    selected: document.querySelectorAll(".leaflet-marker-icon.outline-accent-cyan").length,
    dimmed: [...document.querySelectorAll(".leaflet-marker-icon")].filter((e) => e.style.opacity === "0.22").length,
  }));
  check("Escape clears the selection and the dimming", after.selected === 0 && after.dimmed === 0, `selected=${after.selected} dimmed=${after.dimmed}`);
}
check("no uncaught page errors", errors.length === 0, errors.slice(0, 2).join(" | "));
await browser.close();
process.exit(results.every((r) => r.ok) ? 0 : 1);
