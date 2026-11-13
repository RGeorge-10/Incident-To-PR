// Browser smoke test for the simulation lab. Drives the real UI through both operating modes:
//   automatic detection -> analysis -> human approval -> pull request -> merge (simulated GitHub)
//   -> Argo sync -> sustained-recovery verification, plus a manual report that must escalate.
//
// Prerequisites: `python scripts/sim.py --fresh` is running, and Playwright is installed
// (`npx playwright install chromium` once). Run: node frontend/e2e/smoke.mjs [baseUrl] [screenshotDir]
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const base = process.argv[2] || "http://127.0.0.1:3000";
const shots = process.argv[3] || "";
if (shots) mkdirSync(shots, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: shots ? 2 : 1 });
const page = await context.newPage();
const problems = [];
page.on("pageerror", error => problems.push(`pageerror: ${error.message}`));
page.on("console", message => { if (message.type() === "error" && !/fonts\.g|Failed to load resource/.test(message.text())) problems.push(`console: ${message.text()}`); });
const shot = async name => { if (shots) await page.screenshot({ path: join(shots, `${name}.png`), fullPage: true }); };
const step = async (name, action) => { process.stdout.write(`• ${name} … `); await action(); console.log("ok"); };
const nav = name => page.getByRole("navigation").getByRole("button", { name }).click();

await step("reset the simulated world", async () => {
  const response = await context.request.post(`${base}/sim/reset`);
  if (!response.ok()) throw new Error(`simulator not reachable through ${base}/sim (${response.status()})`);
});

await step("overview renders a healthy fleet", async () => {
  await page.goto(base);
  await page.getByRole("heading", { name: /All 12 services are healthy/ }).waitFor();
  await page.getByText("Simulation lab.", { exact: false }).first().waitFor();
  await shot("01-overview-healthy");
});

await step("enable automatic detection", async () => {
  await nav("Connections & policy");
  await page.getByText("Automatic detection", { exact: true }).last().click();
  await page.getByRole("button", { name: "Apply monitoring settings" }).click();
  await page.getByText("Automatic detection is on", { exact: false }).waitFor();
  await page.getByText("Auto-detection on").waitFor();
  await shot("02-connections");
});

await step("inject a capacity fault and wait for the automatic incident", async () => {
  await nav("Simulation lab");
  await page.getByLabel("Target deployment").selectOption("shippingservice");
  await page.getByRole("button", { name: /Capacity \(infrastructure\)/ }).click();
  await page.getByText("1 faulted").waitFor();
  await shot("03-simulation-lab");
  await nav("Overview");
  await page.getByRole("heading", { name: /1 of 12 services need attention/ }).waitFor({ timeout: 20000 });
  await page.locator(".activity-row", { hasText: "shippingservice" }).waitFor({ timeout: 40000 });
  await shot("04-overview-degraded");
  await page.locator(".activity-row", { hasText: "shippingservice" }).click();
  await page.getByText("Auto-detected", { exact: false }).first().waitFor();
});

await step("analyze: evidence, diagnosis, policy gate, proposal", async () => {
  await page.getByRole("button", { name: "Analyze incident" }).click();
  await page.locator(".detail-head .badge", { hasText: "Needs your review" }).waitFor({ timeout: 40000 });
  await page.getByText("Infrastructure fix").first().waitFor();
  await page.locator(".diff-line.add", { hasText: "replicas: 3" }).waitFor();
  await page.locator(".checks li.pass", { hasText: "Path allowlist" }).waitFor();
  await shot("05-incident-awaiting-review");
});

await step("a pull request cannot be opened before approval", async () => {
  if (await page.getByRole("button", { name: "Open pull request" }).count()) throw new Error("PR button visible before approval");
  if (!(await page.getByRole("button", { name: "Approve proposal" }).isDisabled())) throw new Error("approval possible without a reviewer name");
});

await step("approve and open the pull request", async () => {
  await page.getByLabel("Reviewer name").fill("smoke-test");
  await page.getByRole("button", { name: "Approve proposal" }).click();
  await page.getByRole("button", { name: "Open pull request" }).click();
  await page.locator(".detail-head .badge", { hasText: "PR open" }).waitFor();
  await shot("06-incident-pr-open");
});

await step("merge on the simulated GitHub page (the human reviewer)", async () => {
  const [github] = await Promise.all([context.waitForEvent("page"), page.getByRole("link", { name: /View PR #1/ }).click()]);
  await github.getByRole("heading", { name: /Incident .* INFRA_FIX for shippingservice/ }).waitFor();
  if (shots) await github.screenshot({ path: join(shots, "07-simulated-github-pr.png"), fullPage: true });
  await github.getByRole("button", { name: "Merge pull request" }).click();
  await github.getByText("Merged", { exact: true }).waitFor();
  await github.close();
});

await step("refresh: Argo CD syncs and the fleet recovers", async () => {
  await page.waitForTimeout(8000);
  await page.getByRole("button", { name: "Refresh status" }).click();
  await page.locator(".detail-head .badge", { hasText: /Deployed · observing|Verifying recovery/ }).waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Check recovery" }).click();
  await page.locator(".window").first().waitFor();
  await page.getByText("Synced + Healthy", { exact: true }).waitFor({ timeout: 20000 });
  await shot("08-incident-verifying");
});

await step("manual report without supporting telemetry escalates", async () => {
  await page.getByRole("button", { name: "Report incident" }).click();
  await page.getByLabel("Service / deployment name").fill("frontend");
  await page.getByLabel("What are you seeing?").fill("The storefront feels slow for some users.");
  await page.getByRole("button", { name: "Create incident" }).click();
  await page.getByRole("button", { name: "Analyze incident" }).click();
  await page.locator(".detail-head .badge", { hasText: "Escalated to human" }).waitFor({ timeout: 40000 });
  await shot("09-incident-escalated");
});

await step("recovery is verified only after the sustained window", async () => {
  await page.locator(".queue-item", { hasText: "shippingservice" }).click();
  for (let attempt = 0; attempt < 40; attempt++) {
    await page.getByRole("button", { name: "Check recovery" }).click();
    await page.waitForTimeout(5000);
    if (await page.locator(".detail-head .badge", { hasText: "Recovery verified" }).count()) break;
  }
  await page.locator(".detail-head .badge", { hasText: "Recovery verified" }).waitFor({ timeout: 5000 });
  await shot("10-incident-verified");
  await nav("Overview");
  await page.getByRole("heading", { name: /All 12 services are healthy/ }).waitFor();
  await shot("11-overview-after");
});

await step("narrow viewport has no horizontal overflow", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (overflow > 1) throw new Error(`page overflows horizontally by ${overflow}px`);
  await shot("12-mobile-overview");
});

await browser.close();
if (problems.length) { console.error("\nBrowser errors:\n" + problems.join("\n")); process.exit(1); }
console.log("\nSmoke test passed.");
