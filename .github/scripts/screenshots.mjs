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
    await page.getByRole("button", { name: "Fit" }).click();
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
