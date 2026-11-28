// Tests of the app's page (app/ui/) in Chromium, with a stand-in for the app's core.
//
//     node app/tests/ui.test.mjs [--shots DIR]
//
// Needs Playwright (require("playwright") or npm i playwright). --shots saves a screenshot
// of each screen, in both layouts and every theme, to look at.
import { createRequire } from "node:module";
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const here = dirname(fileURLToPath(import.meta.url));
const page_url = pathToFileURL(join(here, "..", "ui", "index.html")).href;
const fixture = (name) => JSON.parse(readFileSync(join(here, "fixtures", name), "utf8"));
const shots = process.argv.includes("--shots") ? process.argv[process.argv.indexOf("--shots") + 1] : "";
if (shots) mkdirSync(shots, { recursive: true });

const PROJECT = { dir: "C:/boards/riego/hardware/riego-nodo-base", work: "C:/boards/riego", name: "riego-nodo-base" };

// The core: answers the page's commands from `state`, records them in window.calls.
function mockCore(initial) {
  window.calls = [];
  let state = initial;
  const handlers = [];
  window.KH_MOCK = {
    async invoke(name, args) {
      window.calls.push([name, args]);
      if (name === "set_theme") state = { ...state, theme: args.theme };
      if (name === "set_mode") state = { ...state, mode: args.mode };
      if (name === "check_now") state = { ...state, checking: true };
      if (name === "choose_project") state = { ...state, project: window.NEXT_PROJECT || state.project };
      if (name === "show_in_kicad") return window.SHOWN || { ok: true, message: "Selected 2 items in KiCad." };
      return state;
    },
    async listen(event, handler) { handlers.push(handler); },
  };
  window.pushState = (s) => { state = s; handlers.forEach((h) => h({ payload: s })); };
}

const browser = await chromium.launch();
let failures = 0;

async function open(state, width = 1280, height = 900) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.on("pageerror", (e) => { failures++; console.error("page error:", e.message); });
  await page.addInitScript(mockCore, state);
  await page.goto(page_url);
  await page.waitForSelector("#app > *", { state: "attached" });
  return page;
}

async function test(name, fn) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    failures++;
    console.error(`FAIL ${name}\n${e.stack}`);
  }
}

const base = { project: PROJECT, theme: "green", mode: "full", checking: false, problem: null,
               features: { show_in_kicad: false }, status: fixture("status-tidy.json") };

await test("first screen asks for the project", async () => {
  const page = await open({ ...base, project: null, status: null });
  assert.match(await page.textContent("h1"), /Pick your KiCad project/);
  await page.click("text=Choose project folder");
  assert.deepEqual((await page.evaluate(() => window.calls)).map((c) => c[0]), ["get_state", "choose_project"]);
  if (shots) await page.screenshot({ path: join(shots, "welcome.png") });
  await page.close();
});

await test("full window: headline, five pads, the list", async () => {
  const page = await open(base);
  assert.equal(await page.textContent(".head .headline"), "No errors. Six small things left to tidy up.");
  assert.match(await page.textContent(".head .when"), /Checked at .*, in 16 seconds\./);
  assert.equal(await page.textContent(".head .meta"), "riego-nodo-base · main · 744cb38");
  const pads = await page.$$eval(".head .summary .pad", (els) => els.map((e) => [e.className, e.textContent.trim()]));
  assert.deepEqual(pads, [["pad ok", ""], ["pad todo", "5"], ["pad ok", ""], ["pad ok", ""], ["pad todo", "1"]]);
  const rows = await page.$$eval(".row", (els) => els.map((e) => e.querySelector(".chip").textContent));
  assert.deepEqual(rows, ["TP2 · U1", "Q1 · TP4", "RTEMP2", "TP4 · J3", "D2", "D3"]);
  assert.match(await page.textContent(".card-head"), /None of this stops you ordering/);
  assert.equal(await page.$$eval(".show", (e) => e.length), 0); // Show in KiCad: not offered yet
  assert.equal(await page.isVisible(".side-head"), false);
  // the layout fits: nothing wider than the window
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.close();
});

await test("every theme, both layouts", async () => {
  for (const theme of ["green", "blue", "purple", "red", "black"]) {
    for (const [layout, width, height] of [["full", 1280, 900], ["side", 400, 1000]]) {
      const page = await open({ ...base, theme }, width, height);
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), theme);
      const ground = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      assert.notEqual(ground, "rgba(0, 0, 0, 0)");
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${theme} ${layout} too wide`);
      if (shots) await page.screenshot({ path: join(shots, `${layout}-${theme}.png`), fullPage: layout === "side" });
      await page.close();
    }
  }
});

await test("side strip: header on the mask, the full window from its button", async () => {
  const page = await open(base, 400, 1000);
  assert.equal(await page.isVisible(".side-head"), true);
  assert.equal(await page.isVisible(".nav"), false);
  assert.match(await page.textContent(".side-head .when"), /It checks again when you save\./);
  await page.click("[aria-label='Open the full window']");
  assert.deepEqual((await page.evaluate(() => window.calls)).at(-1), ["set_mode", { mode: "full" }]);
  await page.close();
});

await test("picking a theme changes it at once and tells the core", async () => {
  const page = await open(base);
  await page.click(".nav [aria-label='Matte black theme']");
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "black");
  assert.deepEqual((await page.evaluate(() => window.calls)).at(-1), ["set_theme", { theme: "black" }]);
  assert.equal(await page.getAttribute(".nav [aria-label='Matte black theme']", "aria-pressed"), "true");
  await page.close();
});

await test("errors come with their flag and the note changes", async () => {
  const status = fixture("status-example.json");
  const page = await open({ ...base, status });
  assert.match(await page.textContent(".head .headline"), /errors? to fix before you order/);
  assert.match(await page.textContent(".card-head"), /Fix the ones marked first/);
  assert.ok((await page.$$(".row.blocking .flag")).length > 0);
  const square = await page.$$eval(".pad.error", (e) => e.length);
  assert.ok(square >= 1);
  if (shots) {
    await page.screenshot({ path: join(shots, "full-errors.png"), fullPage: true });
    await page.setViewportSize({ width: 400, height: 1000 });
    await page.screenshot({ path: join(shots, "side-errors.png"), fullPage: true });
  }
  await page.close();
});

await test("checking: pads blink and Check now waits", async () => {
  const page = await open(base);
  await page.click("text=Check now");
  assert.ok(await page.$eval("#app", (e) => e.classList.contains("checking")));
  assert.equal(await page.isDisabled("text=Check now"), true);
  assert.match(await page.textContent(".head .when"), /Checking again/);
  await page.evaluate((s) => window.pushState({ ...s, checking: false }), base);  // the core says it is done
  assert.equal(await page.isDisabled("text=Check now"), false);
  await page.close();
});

await test("a check that could not run says why", async () => {
  const failed = { version: 1, state: "failed", headline: "The check could not run.", detail: "kicad-cli not found: install KiCad",
                   indicators: [], items: [] };
  const page = await open({ ...base, status: failed });
  assert.equal(await page.textContent(".head .headline"), "The check could not run.");
  assert.match(await page.textContent(".problem"), /kicad-cli not found/);
  await page.close();
});

await test("Show in KiCad: the button, KiCad's answer under the row", async () => {
  const page = await open({ ...base, features: { show_in_kicad: true } });
  await page.click(".row[data-id='r2'] .show");
  assert.deepEqual((await page.evaluate(() => window.calls)).at(-1), ["show_in_kicad", { id: "r2" }]);
  await page.waitForSelector(".row[data-id='r2'] .said.good");
  assert.equal(await page.textContent(".row[data-id='r2'] .said"), "Selected 2 items in KiCad.");
  await page.evaluate(() => { window.SHOWN = { ok: false, message: "KiCad doesn't answer. Is it open?" }; });
  await page.click(".row[data-id='r5'] .show");
  await page.waitForSelector(".row[data-id='r5'] .said.bad");
  assert.equal(await page.$$eval(".said", (e) => e.length), 1); // only the last answer
  if (shots) await page.screenshot({ path: join(shots, "show-in-kicad.png") });
  await page.close();
});

await test("Show in KiCad in the strip: tap the row", async () => {
  const page = await open({ ...base, features: { show_in_kicad: true } }, 400, 1000);
  await page.click(".row[data-id='r3'] .row-title");
  assert.deepEqual((await page.evaluate(() => window.calls)).at(-1), ["show_in_kicad", { id: "r3" }]);
  assert.equal(await page.isVisible("text=Tap one and KiCad selects it on the board."), true);
  await page.close();
});

await test("text from the files is never taken for markup", async () => {
  const status = fixture("status-tidy.json");
  status.items[0].title = '<img src=x onerror="window.hacked=1">';
  const page = await open({ ...base, status });
  assert.equal(await page.evaluate(() => window.hacked), undefined);
  assert.match(await page.textContent(".row-title"), /<img/);
  await page.close();
});

await browser.close();
if (failures) {
  console.error(`${failures} failed`);
  process.exit(1);
}
console.log("all passed");
