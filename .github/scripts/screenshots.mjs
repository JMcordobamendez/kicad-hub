// Screenshots of the design portal and the PR comment for docs/screenshots/.
// Runs in the "Docs screenshots" workflow: SITE, REPO and PR come from its inputs.
import { chromium } from "playwright";

const { SITE, REPO, PR, OUT = "shots" } = process.env;
const page_url = `${SITE}/pr-${PR}/`;
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: "light" });
const page = await ctx.newPage();

async function open(url, wait = 8000) {
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(wait);  // KiCanvas parses the design after load
}

// KiCanvas shows "Click or tap to interact" until the first click: click once on an
// empty spot, then on a point given as a fraction of the viewer (fx, fy)
async function clickIn(sel, fx, fy) {
  const box = await page.locator(sel).boundingBox();
  if (!box) return;
  await page.mouse.click(box.x + 8, box.y + box.height - 8);
  await page.waitForTimeout(800);
  if (fx != null) await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
  await page.waitForTimeout(1500);
  await page.mouse.move(box.x + box.width + 20, box.y + 20);  // no hover box in the shot
}

// PCB with a part selected (the nPM1300 PMIC, left of centre on the example board)
await open(page_url + "#pcb");
await clickIn("#kc-pcb", 0.336, 0.568);
await page.screenshot({ path: `${OUT}/portal-pcb.png` });

await open(page_url + "#schematic");
const sheet = page.locator("select").filter({ hasText: "nRF54L15" }).first();
if (await sheet.count()) {
  const opt = await sheet.locator("option", { hasText: "nRF54L15" }).first().getAttribute("value");
  await sheet.selectOption(opt);
  await page.waitForTimeout(3000);
}
await clickIn("#kc-sch");
await page.screenshot({ path: `${OUT}/portal-schematic.png` });

// Interactive compare of the first changed page, swipe at the middle
await open(page_url + "#changes", 4000);
const cmp = page.locator(".cmp").first();
if (await cmp.count()) {
  await cmp.scrollIntoViewIfNeeded();
  await page.waitForTimeout(1500);
  await cmp.screenshot({ path: `${OUT}/portal-compare.png` });
}

await open(page_url + "#bom", 3000);
await page.screenshot({ path: `${OUT}/portal-bom.png` });

// The bot's comment on the pull request
await open(`https://github.com/${REPO}/pull/${PR}`, 3000);
const comment = page.locator(".timeline-comment, .react-issue-comment, [data-testid='comment-viewer-outer-box']")
  .filter({ hasText: "Open this design in the browser" }).first();
if (await comment.count()) await comment.screenshot({ path: `${OUT}/pull-request.png` });
else await page.screenshot({ path: `${OUT}/pull-request.png`, fullPage: true });

await browser.close();
