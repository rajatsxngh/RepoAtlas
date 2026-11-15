/**
 * Record the demo that needs no model (LCH-5; decision 0025), on a running local stack:
 *
 *     node scripts/dev.mjs                 # in the repository root, in another terminal
 *     cd frontend
 *     node scripts/record-demo.mjs --base-url http://localhost:3000 --out ../docs/launch
 *
 * It drives the web app with Playwright (the gallery, a map opening, folders opening, search,
 * a symbol's code, and "What breaks?"), records a video, and converts it to `demo.gif` with
 * ffmpeg (which must be on the PATH). It never asks a question, so no model is called and no
 * stand-in answer appears. The gallery must be precomputed first
 * (`repoatlas precompute --list ../launch/repos.yaml`).
 *
 * Options: --base-url (default http://localhost:3000), --out (default ../docs/launch),
 * --repo (the gallery card to open, default psf/requests), --open (folders to open, as node
 * keys separated by commas), --symbol (what to search for, default set_environ: the target of
 * the benchmark question requests-impact-003), --keep-video (keep the .webm next to the GIF).
 */

import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, renameSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "@playwright/test";

const { values: options } = parseArgs({
  options: {
    "base-url": { type: "string", default: "http://localhost:3000" },
    out: { type: "string", default: "../docs/launch" },
    repo: { type: "string", default: "psf/requests" },
    open: { type: "string", default: "dir:src,dir:src/requests" },
    symbol: { type: "string", default: "set_environ" },
    "keep-video": { type: "boolean", default: false },
  },
});

const SIZE = { width: 1280, height: 800 };
const GIF_WIDTH = 800;
const GIF_FPS = 5;

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A caption bar at the bottom of the page, so the GIF explains itself without sound. */
async function caption(page, text) {
  await page.evaluate((message) => {
    let bar = document.getElementById("demo-caption");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "demo-caption";
      Object.assign(bar.style, {
        position: "fixed",
        left: "50%",
        bottom: "20px",
        transform: "translateX(-50%)",
        zIndex: "2147483647",
        padding: "10px 18px",
        borderRadius: "10px",
        background: "rgba(17, 24, 39, 0.92)",
        color: "#fff",
        font: "500 17px/1.3 system-ui, sans-serif",
        boxShadow: "0 6px 24px rgba(0,0,0,0.25)",
        pointerEvents: "none",
        maxWidth: "80vw",
        textAlign: "center",
      });
      document.body.appendChild(bar);
    }
    bar.textContent = message;
  }, text);
}

/** Wait until the map is drawn and nothing waits for layout. */
async function mapReady(page) {
  await page.waitForFunction(
    () => {
      const state = document.querySelector('[data-testid="map-state"]');
      return (
        state && Number(state.dataset.renderedNodes) > 0 && state.dataset.layoutPending === "0"
      );
    },
    undefined,
    { timeout: 60_000 },
  );
}

async function openNode(page, key) {
  const node = page.locator(`.react-flow__node[data-id="${key}"]`);
  await node.locator('button[aria-label^="Open "]').first().click();
  await mapReady(page);
}

async function record(page) {
  await page.goto(options["base-url"]);
  const gallery = page.getByRole("region", { name: "Or open one that is already mapped" });
  await gallery.waitFor({ timeout: 30_000 });
  await caption(page, "RepoAtlas: paste a GitHub URL, or open a repository that is already mapped");
  await pause(2200);
  const card = gallery.getByRole("link", { name: `Open the map of ${options.repo}` });
  await card.scrollIntoViewIfNeeded();
  await card.hover();
  await pause(1200);
  await card.click();

  await page.waitForURL(/\/s\/\d+$/);
  await mapReady(page);
  await caption(page, "The map is built from the code by static analysis. No model draws it");
  await pause(2500);
  const banner = page.getByRole("region", { name: "Map freshness" });
  if (await banner.count()) await banner.getByRole("button", { name: "Dismiss" }).click();
  const overview = page.getByRole("complementary", { name: "Start here" });
  if (await overview.count()) {
    await pause(1500);
    await overview.getByRole("button", { name: "Close overview" }).click();
  }

  await caption(page, "Folders open into files, files into classes and functions");
  for (const key of options.open.split(",").filter(Boolean)) {
    if (await page.locator(`.react-flow__node[data-id="${key}"]`).count()) {
      await openNode(page, key);
      await pause(1500);
    }
  }

  await caption(page, "Search finds any symbol");
  const search = page.getByPlaceholder("Search functions, classes, files…");
  await search.click();
  await search.pressSequentially(options.symbol, { delay: 70 });
  const option = page.getByRole("option").first();
  await option.waitFor();
  await pause(1200);
  await option.click();
  const panel = page.getByRole("complementary", { name: "Node details" });
  await panel.waitFor();
  await mapReady(page);
  await caption(page, "Every box opens to its source, at the exact commit");
  await pause(3000);

  await caption(page, "What breaks if I change this? A graph traversal: no model, $0");
  await pause(800);
  await panel.getByRole("button", { name: "What breaks?" }).click();
  const impact = page.getByRole("complementary", { name: "What breaks" });
  await impact.waitFor();
  await mapReady(page);
  await pause(3500);
  const first = impact.getByRole("region", { name: "Callers" }).locator("[title]").first();
  if (await first.count()) {
    await first.click();
    await caption(page, "Each result shows the path and the lines that connect it");
    await pause(3500);
  }
  await caption(page, "Recorded on a local stack. Answers with citations need a live model");
  await pause(2500);
}

function toGif(video, gif) {
  const filter =
    `fps=${GIF_FPS},scale=${GIF_WIDTH}:-1:flags=lanczos,split[a][b];` +
    "[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle";
  const result = spawnSync(
    "ffmpeg",
    ["-y", "-loglevel", "error", "-i", video, "-vf", filter, gif],
    {
      stdio: "inherit",
    },
  );
  if (result.error) throw new Error(`ffmpeg could not be started: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`ffmpeg failed with exit code ${result.status}.`);
}

const outDir = path.resolve(options.out);
mkdirSync(outDir, { recursive: true });
const videoDir = mkdtempSync(path.join(tmpdir(), "repoatlas-demo-"));
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: SIZE,
  colorScheme: "light",
  recordVideo: { dir: videoDir, size: SIZE },
});
const page = await context.newPage();
// Any request that would ask a model fails the recording: the demo must not need one.
const asked = [];
page.on("request", (request) => {
  if (request.method() === "POST" && /\/(ask|explain)$/.test(new URL(request.url()).pathname)) {
    asked.push(request.url());
  }
});

let failure = null;
try {
  await record(page);
} catch (error) {
  failure = error;
}
const video = await page.video().path();
await context.close();
await browser.close();

try {
  if (failure) throw failure;
  if (asked.length) throw new Error(`The recording asked a model: ${asked.join(", ")}`);
  const gif = path.join(outDir, "demo.gif");
  toGif(video, gif);
  if (options["keep-video"]) renameSync(video, path.join(outDir, "demo.webm"));
  const megabytes = (statSync(gif).size / 1_048_576).toFixed(1);
  console.log(`Wrote ${gif} (${megabytes} MB).`);
} finally {
  rmSync(videoDir, { recursive: true, force: true });
}
