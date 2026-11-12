// Renders an explainer with headless Chrome + ffmpeg. No model involved.
//
// Runs on a work folder (EXPLAINER_WORK, default: the current directory) that holds script.json,
// timings.json, audio/<scene>.wav and scenes/<scene>.js. The work folder has a small render.mjs
// that imports this file, so scene authors run `node render.mjs ...` from there.
//
//   node render.mjs preview s03 1.5 8 20   PNG stills of scene s03 at those seconds -> out/preview/
//   node render.mjs beats s03              one still at the end of every beat of s03 -> out/preview/
//   node render.mjs video s01 s02 ...      out/<scene>.mp4 with its narration (4 scenes in parallel)
//   node render.mjs concat <file.mp4>      joins every scene into one MP4, with chapters and a title
//   node render.mjs qa                     960 px stills at the end of every beat -> out/qa/ + index.json
//   node render.mjs svg <in.svg> <out.png> rasterises an SVG (the solution diagram)
//
// Browser: EXPLAINER_CHROME (a path, or "auto": system Chrome/Chromium if found, else the Chromium
// that `playwright-core install chromium` downloaded).
import { spawn } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORK = resolve(process.env.EXPLAINER_WORK || process.cwd());
const FPS = 30;
const PARALLEL = 4;

function chromePath() {
  const want = process.env.EXPLAINER_CHROME;
  if (want && want !== "auto") return want;
  const candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  ];
  return candidates.find((p) => existsSync(p)); // undefined: Playwright's own download
}

const [mode, ...args] = process.argv.slice(2);
const MODES = ["preview", "beats", "video", "concat", "qa", "svg"];
if (!MODES.includes(mode)) {
  console.log("usage: node render.mjs preview <scene> <t...> | beats <scene> | video <scene...> | concat <out.mp4> | qa | svg <in.svg> <out.png>");
  process.exit(2);
}

const browser = await chromium.launch({ executablePath: chromePath(), headless: true });
let failed = false;

try {
  if (mode === "svg") await svgToPng(args[0], args[1]);
  else await explainer();
} catch (e) {
  console.error(e.message || e);
  failed = true;
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);

// ------------------------------------------------------------------ explainer modes

async function explainer() {
  const script = JSON.parse(readFileSync(join(WORK, "script.json"), "utf8"));
  const timings = JSON.parse(readFileSync(join(WORK, "timings.json"), "utf8"));
  // the backend puts (read-only) copies in the work folder; standalone use gets them here
  for (const f of ["player.html", "lib.js"]) if (!existsSync(join(WORK, f))) copyFileSync(join(HERE, f), join(WORK, f));
  writeFileSync(join(WORK, "data.js"), `window.SCRIPT=${JSON.stringify(script)};window.TIMINGS=${JSON.stringify(timings)};`);
  const ids = script.scenes.map((s) => s.id);
  const known = (id) => {
    if (!timings[id]) throw new Error(`unknown scene ${id} (scenes: ${ids.join(", ")})`);
    return id;
  };

  if (mode === "preview" || mode === "beats") {
    const id = known(args[0]);
    mkdirSync(join(WORK, "out", "preview"), { recursive: true });
    const page = await openPage();
    const times = mode === "beats" ? timings[id].beats.map((b) => b.end) : args.slice(1).map(Number);
    for (const t of times) {
      const file = join(WORK, "out", "preview", `${id}_${t.toFixed(1)}.png`);
      const err = await page.evaluate(([id, t]) => window.renderAt(id, t), [id, t]);
      if (err) console.error(`${id} @${t}s: ${err}`);
      await page.locator("#stage").screenshot({ path: file });
      console.log(`out/preview/${id}_${t.toFixed(1)}.png`);
    }
  } else if (mode === "video") {
    const queue = args.map(known);
    mkdirSync(join(WORK, "out"), { recursive: true });
    await Promise.all(Array.from({ length: PARALLEL }, async () => {
      while (queue.length) await video(queue.shift(), timings);
    }));
  } else if (mode === "concat") {
    await concat(script, timings, resolve(args[0] || join(WORK, "out", "explainer.mp4")));
  } else if (mode === "qa") {
    const dir = join(WORK, "out", "qa");
    mkdirSync(dir, { recursive: true });
    const page = await openPage(0.5);
    const index = [];
    for (const sc of script.scenes) {
      const beats = timings[sc.id].beats;
      for (let i = 0; i < beats.length; i++) {
        const t = Math.max(0, beats[i].end - 0.05);
        const err = await page.evaluate(([id, t]) => window.renderAt(id, t), [sc.id, t]);
        const file = `out/qa/${sc.id}_b${i + 1}.png`;
        await page.locator("#stage").screenshot({ path: join(WORK, file) });
        index.push({ file, scene: sc.id, beat: i + 1, t: +t.toFixed(2), say: beats[i].say, show: sc.beats[i]?.show || "", error: err || undefined });
      }
    }
    writeFileSync(join(dir, "index.json"), JSON.stringify(index, null, 1));
    console.log(`out/qa/index.json (${index.length} frames)`);
  }
}

async function openPage(scale = 1) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: scale });
  page.on("pageerror", (e) => console.error("page error:", e.message));
  await page.goto("file://" + join(WORK, "player.html"));
  await page.waitForFunction(() => window.renderAt);
  return page;
}

function run(cmd, argv, stdin = false) {
  const p = spawn(cmd, argv, { stdio: [stdin ? "pipe" : "ignore", "ignore", "pipe"] });
  let err = "";
  p.stderr.on("data", (d) => (err += d));
  p.done = new Promise((ok, fail) => {
    p.on("error", fail);
    p.on("close", (c) => (c ? fail(new Error(`${cmd} failed: ${err.slice(-800)}`)) : ok()));
  });
  return p;
}

async function video(id, timings) {
  const page = await openPage();
  const dur = timings[id].duration, frames = Math.ceil(dur * FPS);
  const out = join(WORK, "out", `${id}.mp4`);
  const ff = run("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-i", join(WORK, "audio", `${id}.wav`), "-map_metadata", "-1",
    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", String(FPS),
    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-t", String(dur), out], true);
  let firstErr = null;
  for (let f = 0; f < frames; f++) {
    const err = await page.evaluate(([id, t]) => window.renderAt(id, t), [id, f / FPS]);
    if (err && !firstErr) { firstErr = err; console.error(`${id} @${(f / FPS).toFixed(2)}s: ${err}`); }
    const buf = await page.locator("#stage").screenshot({ type: "jpeg", quality: 92 });
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
    if (f % 15 === 0 || f === frames - 1) console.log(`progress ${id} ${f + 1} ${frames}`);
  }
  ff.stdin.end();
  await ff.done;
  await page.close();
  console.log(`out/${id}.mp4 (${dur.toFixed(1)}s, ${frames} frames)`);
}

// ffmetadata values escape = ; # \ and newlines
function meta(s) {
  return String(s).replace(/[=;#\\\n]/g, (c) => (c === "\n" ? " " : "\\" + c));
}

async function concat(script, timings, out) {
  const ids = script.scenes.map((s) => s.id);
  const list = join(WORK, "out", "list.txt");
  writeFileSync(list, ids.map((id) => `file '${join(WORK, "out", id + ".mp4")}'`).join("\n"));
  let t = 0, chapters = "";
  for (const sc of script.scenes) {
    const d = timings[sc.id].duration * 1000;
    chapters += `\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=${Math.round(t)}\nEND=${Math.round(t + d)}\ntitle=${meta(sc.title)}\n`;
    t += d;
  }
  const metaFile = join(WORK, "out", "metadata.txt");
  writeFileSync(metaFile, `;FFMETADATA1\ntitle=${meta(script.title)}\n${chapters}`);
  await run("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list, "-i", metaFile,
    "-map", "0", "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", "-movflags", "+faststart", out]).done;
  console.log(`done: ${out}`);
}

// ------------------------------------------------------------------ diagram

async function svgToPng(src, dst) {
  const svg = readFileSync(resolve(src), "utf8");
  const m = svg.match(/viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/);
  const [w, h] = m ? [Math.ceil(+m[1]), Math.ceil(+m[2])] : [1600, 900];
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  await page.setContent(`<html><body style="margin:0">${svg}</body></html>`);
  await page.locator("svg").first().screenshot({ path: resolve(dst) });
  console.log(dst);
}
