#!/usr/bin/env node
// Measure overflow and capture screenshots at Poke-like viewports via Chrome CDP.
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { resolve, basename } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const CHROME = process.env.CHROME || "google-chrome";
const VIEWPORTS = [
  { name: "360x480", width: 360, height: 480 },
  { name: "400x530", width: 400, height: 530 },
  { name: "1072x1448", width: 1072, height: 1448 },
];

const files = process.argv.slice(2);
if (!files.length) {
  console.error("Usage: measure_viewports.mjs page1.html page2.html ...");
  process.exit(2);
}

const port = 9222 + Math.floor(Math.random() * 1000);
const userDir = `/tmp/poke-chrome-profile-${process.pid}-${port}`;
const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--hide-scrollbars",
    "--default-background-color=FFFFFFFF",
    `--remote-debugging-port=${port}`,
    "--remote-debugging-address=127.0.0.1",
    `--user-data-dir=${userDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "inherit"] },
);

let id = 0;
let ws;
const pending = new Map();
const eventWaiters = [];

function send(method, params = {}) {
  const mid = ++id;
  ws.send(JSON.stringify({ id: mid, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(mid, { resolve, reject });
    setTimeout(() => {
      if (pending.has(mid)) {
        pending.delete(mid);
        reject(new Error(`Timeout: ${method}`));
      }
    }, 20000);
  });
}

function waitEvent(method, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const w = { method, resolve, reject };
    eventWaiters.push(w);
    w.timer = setTimeout(() => {
      const i = eventWaiters.indexOf(w);
      if (i >= 0) eventWaiters.splice(i, 1);
      reject(new Error("Event timeout: " + method));
    }, timeout);
  });
}

async function waitFor(fn, label) {
  for (let i = 0; i < 50; i++) {
    try {
      const v = await fn();
      if (v) return v;
    } catch {
      // retry
    }
    await sleep(150);
  }
  throw new Error("Timeout waiting for " + label);
}

async function main() {
  const page = await waitFor(async () => {
    const res = await fetch(`http://127.0.0.1:${port}/json/list`);
    if (!res.ok) return null;
    const list = await res.json();
    return list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
  }, "devtools page");

  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve);
    ws.addEventListener("error", reject);
  });
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(`${msg.error.message} (${msg.error.data || ""})`));
      else resolve(msg.result);
    } else if (msg.method) {
      for (let i = eventWaiters.length - 1; i >= 0; i--) {
        if (eventWaiters[i].method === msg.method) {
          const w = eventWaiters.splice(i, 1)[0];
          clearTimeout(w.timer);
          w.resolve(msg.params || {});
        }
      }
    }
  });

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setTouchEmulationEnabled", { enabled: true }).catch(() => {});

  const results = [];
  const outDir = resolve(".");
  const measureExpr = `(() => {
    const root = document.documentElement;
    const body = document.body;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const cw = root.clientWidth;
    const ch = root.clientHeight;
    const sw = Math.max(root.scrollWidth, body ? body.scrollWidth : 0);
    const sh = Math.max(root.scrollHeight, body ? body.scrollHeight : 0);
    const clipped = [];
    for (const el of document.querySelectorAll("body *")) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (r.bottom > vh + 1.5 || r.right > vw + 1.5 || r.top < -1.5 || r.left < -1.5) {
        clipped.push({
          tag: el.tagName.toLowerCase(),
          className: String(el.className || "").slice(0, 80),
          text: (el.innerText || "").replace(/\\s+/g, " ").slice(0, 60),
          top: Math.round(r.top),
          left: Math.round(r.left),
          right: Math.round(r.right),
          bottom: Math.round(r.bottom),
        });
      }
    }
    return {
      innerWidth: vw,
      innerHeight: vh,
      clientWidth: cw,
      clientHeight: ch,
      scrollWidth: sw,
      scrollHeight: sh,
      overflowX: sw > cw + 1,
      overflowY: sh > ch + 1,
      clipped: clipped.slice(0, 12),
      clippedCount: clipped.length,
    };
  })()`;

  for (const file of files) {
    const abs = resolve(file);
    const stem = basename(file, ".html");
    for (const vp of VIEWPORTS) {
      await send("Emulation.setDeviceMetricsOverride", {
        width: vp.width,
        height: vp.height,
        deviceScaleFactor: 1,
        mobile: true,
        screenWidth: vp.width,
        screenHeight: vp.height,
      });
      const loaded = waitEvent("Page.loadEventFired");
      await send("Page.navigate", { url: `file://${abs}` });
      try {
        await loaded;
      } catch (e) {
        console.error(String(e));
      }
      await sleep(250);
      const ev = await send("Runtime.evaluate", { returnByValue: true, expression: measureExpr });
      const metrics = ev.result.value;
      const shot = await send("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: false,
      });
      const shotPath = resolve(outDir, `preview-${stem}-${vp.name}.png`);
      writeFileSync(shotPath, Buffer.from(shot.data, "base64"));
      const row = {
        file: basename(file),
        viewport: vp.name,
        ...metrics,
        screenshot: shotPath,
        ok: !metrics.overflowX && !metrics.overflowY && metrics.clippedCount === 0,
      };
      results.push(row);
      const flag = row.ok ? "OK" : "OVERFLOW";
      console.log(
        `${flag} ${row.file} @${row.viewport} client=${metrics.clientWidth}x${metrics.clientHeight} scroll=${metrics.scrollWidth}x${metrics.scrollHeight} clipped=${metrics.clippedCount}`,
      );
      if (!row.ok && metrics.clipped[0]) {
        console.log("  first clipped:", JSON.stringify(metrics.clipped[0]));
      }
    }
  }

  writeFileSync(resolve(outDir, "fit-report.json"), JSON.stringify(results, null, 2));
  const failed = results.filter((r) => !r.ok);
  if (failed.length) {
    console.error(`FIT CHECK FAILED: ${failed.length} viewport/page pairs overflowed`);
    process.exitCode = 1;
  } else {
    console.log("FIT CHECK PASSED: no overflow at 360x480, 400x530, or 1072x1448");
  }
}

main()
  .catch((err) => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(() => {
    try {
      if (ws) ws.close();
    } catch {}
    try {
      chrome.kill("SIGTERM");
    } catch {}
    setTimeout(() => process.exit(process.exitCode || 0), 400);
  });
