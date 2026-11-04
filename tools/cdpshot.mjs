// CDP screenshot driver: node cdpshot.mjs jobs.json
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PORT = 9333;
const W = 1440, H = 900, DSF = 2;

const jobsFile = process.argv[2];
const jobs = JSON.parse(await fs.readFile(jobsFile, "utf8"));

const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "cdp-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
  "--no-first-run", "--no-default-browser-check", "--disable-extensions",
  "--force-device-scale-factor=2", "--window-size=1440,900",
  "--remote-debugging-port=" + PORT, "--user-data-dir=" + tmp, "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

let wsUrl = null;
for (let i = 0; i < 60; i++) {
  try {
    const r = await fetch("http://127.0.0.1:" + PORT + "/json/version");
    wsUrl = (await r.json()).webSocketDebuggerUrl;
    if (wsUrl) break;
  } catch {}
  await sleep(300);
}
if (!wsUrl) { console.log("chrome did not start"); process.exit(1); }

const ws = new WebSocket(wsUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let id = 0;
const pending = new Map();
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.method === "Page.javascriptDialogOpening") {
    const sid = msg.sessionId;
    ws.send(JSON.stringify({ id: ++id, method: "Page.handleJavaScriptDialog", params: { accept: true }, sessionId: sid }));
    return;
  }
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject, timer } = pending.get(msg.id);
    clearTimeout(timer);
    pending.delete(msg.id);
    if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
  }
};
function send(method, params = {}, sessionId, timeout = 60000) {
  const mid = ++id;
  const payload = { id: mid, method, params };
  if (sessionId) payload.sessionId = sessionId;
  ws.send(JSON.stringify(payload));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(mid); reject(new Error("timeout " + method)); }, timeout);
    pending.set(mid, { resolve, reject, timer });
  });
}

const results = [];
for (const job of jobs) {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const S = sessionId;
  try {
    await send("Page.enable", {}, S);
    await send("Runtime.enable", {}, S);
    await send("Emulation.setDeviceMetricsOverride",
      { width: job.w || W, height: job.h || H, deviceScaleFactor: job.dsf || DSF, mobile: false }, S);
    await send("Page.navigate", { url: job.url }, S, 90000);
    await sleep(job.wait ?? 4000);
    for (const step of (job.steps || [])) {
      const r = await send("Runtime.evaluate",
        { expression: step.js, awaitPromise: true, returnByValue: true }, S, 60000);
      if (step.log) results.push({ job: job.name, log: r.result && r.result.value });
      await sleep(step.wait ?? 1500);
    }
    let clip;
    if (job.rectJS) {
      const r = await send("Runtime.evaluate", { expression: job.rectJS, returnByValue: true }, S, 30000);
      const v = r.result && r.result.value;
      if (v && v.width > 4) {
        const h = job.clipHeight ? Math.min(job.clipHeight, v.height) : v.height;
        clip = { x: Math.round(v.x), y: Math.round(v.y), width: Math.round(v.width), height: Math.round(h), scale: 1 };
      }
      results.push({ job: job.name, rect: v });
    } else if (job.clipSelector) {
      const expr = "(() => { const el = document.querySelector(" + JSON.stringify(job.clipSelector) + "); if (!el) return null; const b = el.getBoundingClientRect(); return { x: b.x + window.scrollX, y: b.y + window.scrollY, width: b.width, height: b.height }; })()";
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true }, S, 30000);
      const v = r.result && r.result.value;
      if (v && v.width > 4 && v.height > 4) {
        const h = job.clipHeight ? Math.min(job.clipHeight, v.height) : v.height;
        clip = { x: Math.round(v.x), y: Math.round(v.y), width: Math.round(v.width), height: Math.round(h), scale: 1 };
      }
      results.push({ job: job.name, rect: v });
    } else if (job.fullPage) {
      const m = await send("Page.getLayoutMetrics", {}, S);
      const cs = m.cssContentSize || m.contentSize;
      clip = { x: 0, y: 0, width: Math.ceil(cs.width), height: Math.ceil(cs.height), scale: 1 };
    }
    const shot = await send("Page.captureScreenshot",
      { format: "png", fromSurface: true, captureBeyondViewport: !!job.fullPage, ...(clip ? { clip } : {}) },
      S, 90000);
    await fs.mkdir(path.dirname(job.path), { recursive: true });
    await fs.writeFile(job.path, Buffer.from(shot.data, "base64"));
    results.push({ job: job.name, saved: job.path, bytes: shot.data.length });
  } catch (e) {
    results.push({ job: job.name, error: String(e).slice(0, 200) });
  } finally {
    try { await send("Target.closeTarget", { targetId }); } catch {}
  }
}
console.log(JSON.stringify(results, null, 1));
ws.close();
chrome.kill();
process.exit(0);
