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

// PCB with a part selected (click the middle of the board)
await open(page_url + "#pcb");
const pcb = page.locator("#kc-pcb");
const box = await pcb.boundingBox();
if (box) await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/portal-pcb.png` });

await open(page_url + "#schematic");
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
