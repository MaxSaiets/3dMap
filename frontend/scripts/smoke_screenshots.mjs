import fs from "node:fs";
import path from "node:path";

import { chromium } from "playwright";

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
  return p;
}

function nowStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`;
}

async function main() {
  // Paton Bridge bbox taken from regression report (realistic “as in reality” test area)
  const bbox = {
    north: 50.43547417601264,
    south: 50.41752236855583,
    east: 30.60682611709925,
    west: 30.5661816359585,
  };

  const outRoot = ensureDir(path.join("..", "backend", "output", "real_smoke"));
  const outDir = ensureDir(path.join(outRoot, `ui_${nowStamp()}`));

  // Next dev may switch to 3001 if 3000 is taken.
  const baseUrl = process.env.FRONTEND_URL || "http://localhost:3000";
  const url =
    `${baseUrl}/?autogen=1` +
    `&north=${bbox.north}&south=${bbox.south}&east=${bbox.east}&west=${bbox.west}`;

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

  page.on("console", (msg) => {
    const t = msg.type();
    if (t === "error") console.error("[browser console]", msg.text());
  });

  console.log("[smoke] opening:", url);
  await page.goto(url, { waitUntil: "domcontentloaded" });

  // Wait until backend generation completes and preview has a downloadUrl set
  // Playwright signature is (fn, arg, options). Pass `null` as arg.
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-testid="model-ready"]');
    return el && el.textContent && el.textContent.trim() === "1";
  }, null, { timeout: 20 * 60 * 1000 });

  // Give the 3D loader a bit of time to fetch and parse meshes
  await page.waitForTimeout(5000);

  const taskId = await page.locator('[data-testid="active-task-id"]').textContent();
  const downloadUrl = await page.locator('[data-testid="download-url"]').textContent();

  console.log("[smoke] taskId:", (taskId || "").trim());
  console.log("[smoke] downloadUrl:", (downloadUrl || "").trim());

  // Screenshots
  await page.screenshot({ path: path.join(outDir, "full.png"), fullPage: true });

  // Top half map region and bottom half 3D region (based on current layout: 50%/50%)
  const viewport = page.viewportSize();
  if (viewport) {
    await page.screenshot({
      path: path.join(outDir, "map_top_half.png"),
      clip: { x: 0, y: 0, width: viewport.width, height: Math.floor(viewport.height / 2) },
    });
    await page.screenshot({
      path: path.join(outDir, "preview_bottom_half.png"),
      clip: { x: 0, y: Math.floor(viewport.height / 2), width: viewport.width, height: Math.floor(viewport.height / 2) },
    });
  }

  // Download the actual 3MF produced by the real API task (so it's “as in reality”)
  if (taskId && taskId.trim()) {
    const filePath = path.join(outDir, `task_${taskId.trim()}.3mf`);
    try {
      // Backend dev server can reload (ECONNRESET). Retry a few times.
      let lastErr = null;
      for (let i = 0; i < 3; i++) {
        try {
          const resp = await page.request.get(`http://localhost:8000/api/download/${taskId.trim()}?format=3mf`, {
            timeout: 120000,
          });
          if (!resp.ok()) throw new Error(`Download failed: ${resp.status()} ${resp.statusText()}`);
          const buf = await resp.body();
          fs.writeFileSync(filePath, buf);
          console.log("[smoke] saved 3MF (download):", filePath);
          lastErr = null;
          break;
        } catch (e) {
          lastErr = e;
          await page.waitForTimeout(2000);
        }
      }
      if (lastErr) throw lastErr;
    } catch (e) {
      // Fallback: copy directly from backend output folder (same file that API would serve)
      const direct = path.join("..", "backend", "output", `${taskId.trim()}.3mf`);
      if (fs.existsSync(direct)) {
        fs.copyFileSync(direct, filePath);
        console.log("[smoke] saved 3MF (filesystem fallback):", filePath);
      } else {
        throw e;
      }
    }
  }

  console.log("[smoke] saved screenshots to:", outDir);
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});


