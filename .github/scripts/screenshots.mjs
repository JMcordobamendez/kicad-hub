// Screenshots of the design portal and the PR comment for docs/screenshots/.
// Runs in the "Docs screenshots" workflow: SITE, REPO and PR come from its inputs.
// PR is a pull request number (its portal page and comment), a site path such as
// "main" or "releases/v1.1.0" (portal shots only), or "none" (extra pages only).
import { chromium } from "playwright";

const { SITE, REPO, PR = "none", OUT = "shots" } = process.env;
const isPR = /^\d+$/.test(PR);
const page_url = isPR ? `${SITE}/pr-${PR}/` : `${SITE}/${PR.replace(/^\/+|\/+$/g, "")}/`;
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: "light" });
const page = await ctx.newPage();
// Console errors of every page, to catch broken JS without a local browser
const errors = [];
page.on("console", m => { if (m.type() === "error") errors.push(`[${page.url()}] ${m.text()}`); });
page.on("pageerror", e => errors.push(`[${page.url()}] ${e.message}`));

// One screenshot step: a failure (missing page, changed layout) is logged, not fatal
async function step(name, fn) {
  try { await fn(); } catch (e) { errors.push(`[step ${name}] ${e.message.split("\n")[0]}`); }
}

async function open(url, wait = 8000) {
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(wait);  // KiCanvas parses the design after load
}

// KiCanvas shows "Click or tap to interact" until the first click: click once on an
// empty spot, then on a point given as a fraction of the viewer (fx, fy)
async function clickIn(sel, fx, fy) {
  const box = await page.locator(sel).boundingBox({ timeout: 5000 });
  if (!box) return;
  await page.mouse.click(box.x + 8, box.y + box.height - 8);
  await page.waitForTimeout(800);
  if (fx != null) await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
  await page.waitForTimeout(1500);
  await page.mouse.move(box.x + box.width + 20, box.y + 20);  // no hover box in the shot
}

if (PR !== "none") await step("pcb", async () => {
  // PCB with a part selected (the nPM1300 PMIC, left of centre on the example board)
  await open(page_url + "#pcb");
  await clickIn("#kc-pcb", 0.336, 0.568);
  await page.screenshot({ path: `${OUT}/portal-pcb.png` });
});

if (PR !== "none") await step("schematic", async () => {
  await open(page_url + "#schematic");
  const sheet = page.locator("select").filter({ hasText: "nRF54L15" }).first();
  if (await sheet.count()) {
    const opt = await sheet.locator("option", { hasText: "nRF54L15" }).first().getAttribute("value");
    await sheet.selectOption(opt);
    await page.waitForTimeout(3000);
  }
  await clickIn("#kc-sch");
  await page.screenshot({ path: `${OUT}/portal-schematic.png` });
});

if (PR !== "none") await step("compare", async () => {
  // Interactive compare of the first changed page, swipe at the middle
  await open(page_url + "#changes", 4000);
  const cmp = page.locator(".cmp").first();
  if (await cmp.count()) {
    await cmp.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    await cmp.screenshot({ path: `${OUT}/portal-compare.png` });
  }
});

if (PR !== "none") await step("bom", async () => {
  await open(page_url + "#bom", 3000);
  await page.screenshot({ path: `${OUT}/portal-bom.png` });
});

if (isPR) await step("pr-comment", async () => {
  // The bot's comment on the pull request
  await open(`https://github.com/${REPO}/pull/${PR}`, 3000);
  const comment = page.locator(".timeline-comment, .react-issue-comment, [data-testid='comment-viewer-outer-box']")
    .filter({ hasText: "Open this design in the browser" }).first();
  if (await comment.count()) await comment.screenshot({ path: `${OUT}/pull-request.png` });
  else await page.screenshot({ path: `${OUT}/pull-request.png`, fullPage: true });
});

// Extra pages to check, from EXTRA="name=url name=url" (e.g. a compare page)
for (const item of (process.env.EXTRA || "").split(/\s+/).filter(Boolean)) {
  const [name, ...rest] = item.split("=");
  await step(`extra ${name}`, async () => {
  await open(rest.join("="), 10000);
  await page.screenshot({ path: `${OUT}/extra-${name}.png`, fullPage: true });
  if (name === "activity") {  // interactions: select a commit, hover a feed item, then a phone
    await page.locator(".row[data-s]").nth(3).click();
    await page.locator(".feed li[data-e]").nth(2).hover();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/extra-${name}-selected.png` });
    await page.check("#f-main");  // a filter, to exercise the redraw
    await page.setViewportSize({ width: 390, height: 800 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/extra-${name}-phone.png`, fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  if (name === "variants") try {  // variant selector (scripts/variants-view.js): open on ?variant=lite#pcb
    const shot = async step => { await page.waitForTimeout(1500); await page.screenshot({ path: `${OUT}/extra-variants-${step}.png` }); };
    const box = await page.locator("#kc-pcb").boundingBox();
    await page.mouse.click(box.x + 8, box.y + box.height - 8);  // first click only wakes KiCanvas
    await page.waitForTimeout(800);
    console.log("url:", page.url(), "| pickers:", await page.locator(".kv-select, .kv-pick select").count(),
      "| legend:", JSON.stringify(await page.locator(".kv-legend").first().textContent()),
      "| overlay polygons:", await page.locator(".kv-dnp polygon").count());
    await page.mouse.move(box.x + box.width + 20, box.y + 20);
    await shot("1-pcb");
    await page.evaluate(() => { viewers.pcb.select("C10"); viewers.pcb.zoom_to_selection(); });
    await shot("2-pcb-c10");
    console.log("C10 card:", JSON.stringify((await page.locator("#sel-pcb").innerText()).slice(0, 300)));
    await page.evaluate(() => { viewers.pcb.select("U5"); viewers.pcb.zoom_to_selection(); });
    await shot("3-pcb-u5");
    console.log("U5 card:", JSON.stringify((await page.locator("#sel-pcb").innerText()).slice(0, 300)));
    await page.getByRole("button", { name: "Bottom" }).click();
    await page.evaluate(() => viewers.pcb.zoom_to_board());
    await shot("4-pcb-bottom");
    await page.getByRole("button", { name: "Top" }).click();
    await page.evaluate(() => viewers.pcb.zoom_to_board());
    await shot("5-pcb-top");
    await page.locator("#s-pcb select.kv-select, #s-pcb .kv-pick select").first().selectOption("");
    await shot("6-pcb-default");
    await page.locator("#s-pcb .kv-pick select").first().selectOption("lite");
    // the schematic: same variant, sheet with the debugger (U5, U6, Y2)
    await page.locator('nav a[data-tab="schematic"]').click();
    await page.waitForTimeout(3000);
    const sheet = page.locator("#sheet-sel");
    if (await sheet.count()) {
      const opt = await sheet.locator("option", { hasText: "Debugger" }).first().getAttribute("value");
      await sheet.selectOption(opt);
      await page.waitForTimeout(3000);
    }
    const sbox = await page.locator("#kc-sch").boundingBox();
    await page.mouse.click(sbox.x + 8, sbox.y + sbox.height - 8);
    await page.waitForTimeout(800);
    console.log("sch picker value:", await page.locator("#s-schematic .kv-pick select").first().inputValue(),
      "| legend:", JSON.stringify(await page.locator("#s-schematic .kv-legend").first().textContent()),
      "| url:", page.url());
    await page.mouse.move(sbox.x + sbox.width + 20, sbox.y + 20);
    await shot("7-sch-lite");
    await page.evaluate(() => { viewers.sch.select("U5"); viewers.sch.zoom_to_selection(); });
    await shot("8-sch-u5");
    console.log("sch U5 card:", JSON.stringify((await page.locator("#sel-sch").innerText()).slice(0, 300)));
    await page.locator("#s-schematic .kv-pick select").first().selectOption("");
    await page.evaluate(() => viewers.sch.zoom_to_page());
    await shot("9-sch-default");
  } catch (e) {
    console.log("variants step failed:", String(e.message).slice(0, 300));
    errors.push(`[variants step] ${String(e.message).slice(0, 300)}`);
    await page.screenshot({ path: `${OUT}/extra-variants-failed.png` });
  }
  if (name === "xprobe") try {  // cross-probing PCB <-> schematic (scripts/crossprobe.js): open on #pcb
    const shot = async step => { await page.waitForTimeout(1200); await page.screenshot({ path: `${OUT}/extra-xprobe-${step}.png` }); };
    const cardText = async id => JSON.stringify((await page.locator(id).innerText()).replace(/\s+/g, " ").slice(0, 400));
    const box = await page.locator("#kc-pcb").boundingBox();
    await page.mouse.click(box.x + 8, box.y + box.height - 8);  // first click only wakes KiCanvas
    await page.waitForTimeout(800);
    await page.mouse.move(box.x + box.width + 20, box.y + 20);
    await page.evaluate(() => { viewers.pcb.select("U5"); viewers.pcb.zoom_to_selection(); });
    await page.waitForTimeout(800);
    console.log("xprobe buttons:", await page.locator("[data-xp]").count(), "| sync buttons:", await page.getByRole("button", { name: "Sync" }).count());
    console.log("PCB U5 card:", await cardText("#sel-pcb"));
    await shot("1-pcb-u5");
    // 1. part, PCB -> schematic
    await page.getByRole("button", { name: "Show in schematic" }).first().click();
    await page.waitForTimeout(6000);
    console.log("part->sch: hash", new URL(page.url()).hash, "| sheet:", await page.locator("#sheet-sel").evaluate(e => e.options[e.selectedIndex]?.text),
      "| selected:", await page.evaluate(() => viewers.sch.selected && viewers.sch.selected.context && viewers.sch.selected.context.reference));
    console.log("sch card:", await cardText("#sel-sch"));
    await shot("2-sch-u5");
    // 2. part, schematic -> PCB (the symbol the sheet has, picked in the viewer)
    await page.evaluate(() => { viewers.sch.select("U6"); });
    await page.waitForTimeout(800);
    await shot("3-sch-u6");
    await page.getByRole("button", { name: "Show on PCB" }).first().click();
    await page.waitForTimeout(3500);
    console.log("part->pcb: hash", new URL(page.url()).hash, "| selected:", await page.evaluate(() => viewers.pcb.selected && viewers.pcb.selected.context && viewers.pcb.selected.context.reference));
    console.log("PCB card:", await cardText("#sel-pcb"));
    await shot("4-pcb-u6");
    // the nets of a symbol, once the board is loaded: Show on PCB highlights one
    await page.locator('nav a[data-tab="schematic"]').click();
    await page.waitForTimeout(1500);
    await page.evaluate(() => { viewers.sch.select("U5"); });
    await page.waitForTimeout(500);
    const sn = page.locator('#sel-sch [data-xp="net-pcb"]');
    console.log("sch net buttons:", await sn.count());
    if (await sn.count()) {
      await sn.nth(1).click();
      await page.waitForTimeout(3500);
      console.log("sch net->pcb: hash", new URL(page.url()).hash, "| net card:", await cardText(".kt-card"));
      await shot("6-pcb-net-from-sch");
      await page.locator('nav a[data-tab="schematic"]').click();
      await page.waitForTimeout(1000);
    }
    // 3. net, PCB -> schematic
    await page.locator('nav a[data-tab="pcb"]').click();
    await page.waitForTimeout(1500);
    await page.evaluate(() => { viewers.pcb.select("U5"); });
    await page.waitForTimeout(500);
    const nets = page.locator("#sel-pcb .netbtn");
    console.log("U5 net buttons:", JSON.stringify(await nets.allInnerTexts()));
    for (const i of [0, 8]) {
      if (i >= await nets.count()) break;
      await nets.nth(i).click();
      await page.waitForTimeout(800);
      await page.locator(".kt-card").getByRole("button", { name: "Show in schematic" }).click();
      await page.waitForTimeout(6000);
      console.log(`net ${i}: hash`, new URL(page.url()).hash, "| net card:", await cardText(".xp-card"), "| pcb msg:", JSON.stringify(await page.locator(".xp-netmsg").first().innerText().catch(() => "")));
      await shot(`7-net${i}-sch`);
      const next = page.getByRole("button", { name: "Next sheet with the net" });
      if (await next.count() && await next.isEnabled()) {
        await next.click();
        await page.waitForTimeout(5000);
        console.log(`net ${i} next sheet:`, await cardText(".xp-card"));
        await shot(`8-net${i}-sch-next`);
      }
      await page.locator(".xp-card").getByRole("button", { name: "Show on PCB" }).click();
      await page.waitForTimeout(3000);
      console.log(`net ${i} on PCB:`, await cardText(".kt-card"));
      await shot(`9-net${i}-pcb`);
      await page.evaluate(() => { viewers.pcb.select("U5"); });
      await page.waitForTimeout(500);
    }
    // multi-unit symbols, if the design has any: Show in schematic opens the first unit, the others are listed
    const multi = await page.evaluate(() => {
      const e = document.getElementById("kc-sch"), p = findProject(e.shadowRoot || e), out = {};
      for (const pg of p.pages()) if (pg.type === "schematic") for (const s of pg.document.symbols.values()) {
        try { if (s.lib_symbol.unit_count > 1) (out[s.reference] ||= []).push(s.unit); } catch (err) { /* no library symbol */ }
      }
      return out;
    });
    console.log("multi-unit refs:", JSON.stringify(multi).slice(0, 300));
    const mref = Object.keys(multi).find(r => /^[A-Z]+\d+$/.test(r) && viewers.pcb);
    if (mref) {
      await page.locator('nav a[data-tab="pcb"]').click();
      await page.waitForTimeout(1000);
      await page.evaluate(r => { viewers.pcb.select(r); }, mref);
      await page.waitForTimeout(500);
      await page.getByRole("button", { name: "Show in schematic" }).first().click();
      await page.waitForTimeout(6000);
      console.log("multi-unit", mref, "sch card:", await cardText("#sel-sch"));
      await shot("11-multi-unit");
    }
    // keyboard: the focus lands on the Selected card after a button, Esc clears the rings
    console.log("active element:", await page.evaluate(() => document.activeElement && (document.activeElement.id || document.activeElement.tagName)));
    // 4. sync mode
    await page.getByRole("button", { name: "Sync" }).first().click();
    console.log("sync url:", page.url());
    await page.evaluate(() => { viewers.pcb.select("C10"); });
    await page.waitForTimeout(1500);
    await page.locator('nav a[data-tab="schematic"]').click();
    await page.waitForTimeout(5000);
    console.log("sync -> sch selected:", await page.evaluate(() => viewers.sch.selected && viewers.sch.selected.context && viewers.sch.selected.context.reference));
    await shot("10-sync-sch");
  } catch (e) {
    console.log("xprobe step failed:", String(e.message).slice(0, 300));
    errors.push(`[xprobe step] ${String(e.message).slice(0, 300)}`);
    await page.screenshot({ path: `${OUT}/extra-xprobe-failed.png` });
  }
  if (name === "xprobevar") try {  // the variant picker (?variant=lite) with cross-probing
    const shot = async step => { await page.waitForTimeout(1200); await page.screenshot({ path: `${OUT}/extra-xprobevar-${step}.png` }); };
    const cardText = async id => JSON.stringify((await page.locator(id).innerText()).replace(/\s+/g, " ").slice(0, 400));
    const box = await page.locator("#kc-pcb").boundingBox();
    await page.mouse.click(box.x + 8, box.y + box.height - 8);
    await page.waitForTimeout(800);
    await page.evaluate(() => { viewers.pcb.select("C10"); viewers.pcb.zoom_to_selection(); });
    await page.waitForTimeout(800);
    await page.getByRole("button", { name: "Show in schematic" }).first().click();
    await page.waitForTimeout(6000);
    console.log("var url:", page.url(), "| sch card:", await cardText("#sel-sch"));
    await shot("1-sch-c10");
    await page.getByRole("button", { name: "Show on PCB" }).first().click();
    await page.waitForTimeout(3500);
    console.log("var pcb card:", await cardText("#sel-pcb"));
    await shot("2-pcb-c10");
    // a part not fitted in the variant: still shown, with a note
    const dnp = await page.evaluate(() => { const v = D.variants.find(x => x.name === "lite"); return v && (v.dnp || [])[0]; });
    console.log("lite dnp part:", dnp);
    if (dnp) {
      await page.evaluate(r => { viewers.pcb.select(r); }, dnp);
      await page.waitForTimeout(500);
      await page.getByRole("button", { name: "Show in schematic" }).first().click();
      await page.waitForTimeout(6000);
      await page.getByRole("button", { name: "Show on PCB" }).first().click();
      await page.waitForTimeout(3500);
      console.log("dnp pcb card:", await cardText("#sel-pcb"));
      await shot("3-pcb-dnp");
    }
    await page.keyboard.press("Tab");
    console.log("tab focus:", await page.evaluate(() => document.activeElement && (document.activeElement.dataset.xp || document.activeElement.id || document.activeElement.tagName)));
  } catch (e) {
    console.log("xprobevar step failed:", String(e.message).slice(0, 300));
    errors.push(`[xprobevar step] ${String(e.message).slice(0, 300)}`);
    await page.screenshot({ path: `${OUT}/extra-xprobevar-failed.png` });
  }
  if (name === "nets") try {  // net highlight, pad walk and measuring (scripts/viewer-tools.js)
    const shot = async step => { await page.waitForTimeout(1200); await page.screenshot({ path: `${OUT}/extra-nets-${step}.png` }); };
    const box = await page.locator("#kc-pcb").boundingBox();
    await page.mouse.click(box.x + 8, box.y + box.height - 8);  // first click only wakes KiCanvas
    await page.waitForTimeout(800);
    await page.mouse.click(box.x + box.width * 0.336, box.y + box.height * 0.568);
    // KiCanvas asks "Footprint or Net?" when both are under the click
    await page.waitForTimeout(800);
    const item = page.getByText(/Footprint: /).first();
    console.log("menu items:", await item.count());
    if (await item.count()) await item.click();
    await shot("1-part");
    const nets = page.locator("#sel-pcb .netbtn");
    console.log("net buttons:", await nets.count(), "| nets card:", await page.locator(".kt-q").count(), "| selected:", JSON.stringify((await page.locator("#sel-pcb").innerText()).slice(0, 200)));
    if (await nets.count()) await nets.nth(Math.min(1, (await nets.count()) - 1)).click();
    await shot("2-net");
    await page.getByRole("button", { name: "Next pad of the net" }).click({ timeout: 5000 });
    await shot("3-pad1");
    await page.getByRole("button", { name: "Next pad of the net" }).click({ timeout: 5000 });
    await shot("4-pad2");
    await page.getByRole("button", { name: "Clear" }).first().click();
    await page.fill(".kt-q", "GND");
    await page.locator(".kt-hit").first().click();
    await shot("5-search-gnd");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Measure" }).click();
    await page.mouse.click(box.x + box.width * 0.30, box.y + box.height * 0.50);
    await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.60);
    await shot("6-measure-live");
    await page.mouse.click(box.x + box.width * 0.45, box.y + box.height * 0.60);
    await shot("7-measure");
    console.log("readout:", await page.locator(".kt-read").first().textContent());
    await page.keyboard.press("Escape");
    await shot("8-cleared");
  } catch (e) {
    console.log("nets step failed:", String(e.message).slice(0, 200));
    errors.push(`[nets step] ${String(e.message).slice(0, 200)}`);
    await page.screenshot({ path: `${OUT}/extra-nets-failed.png` });
  }
  });
}

const fs = await import("node:fs");
fs.writeFileSync(`${OUT}/console-errors.txt`, errors.join("\n") + "\n");
await browser.close();
