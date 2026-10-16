import { Mic } from "/audio.js";
import { Board } from "/board.js";

const $ = (id) => document.getElementById(id);
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
};
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const md = (s) => window.marked && window.DOMPurify ? DOMPurify.sanitize(marked.parse(s || "")) : esc(s).replace(/\n/g, "<br>");
const fmt = (sec) => { sec = Math.max(0, Math.floor(sec)); return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`; };
const dark = () => matchMedia("(prefers-color-scheme: dark)").matches;

const VERDICTS = { "strong hire": "good", "hire": "good", "lean hire": "good", "lean no hire": "bad", "no hire": "bad" };
const STATUS_LABEL = { live: "en curso", evaluating: "evaluando…", ended: "sin evaluar", setup: "preparando" };

let META = null;

// ====================================================================== router
async function route() {
  const m = location.hash.match(/^#\/s\/(.+)$/);
  if (!META) META = await api("/api/meta");
  if (m) openSession(decodeURIComponent(m[1]));
  else showHome();
}
window.addEventListener("hashchange", route);

// ====================================================================== confirm toast
function ask(text, yes = "Sí") {
  return new Promise((resolve) => {
    $("phaseConfirmText").textContent = text;
    $("phaseYes").textContent = yes;
    $("phaseConfirm").hidden = false;
    const done = (v) => { $("phaseConfirm").hidden = true; $("phaseYes").onclick = $("phaseNo").onclick = null; resolve(v); };
    $("phaseYes").onclick = () => done(true);
    $("phaseNo").onclick = () => done(false);
  });
}

// ====================================================================== home
function showHome() {
  leaveSession();
  $("session").hidden = true;
  $("home").hidden = false;
  document.title = "Mock Interview";
  renderSetup();
  renderHistory();
}

function renderSetup() {
  const saved = store.get("lastPack", META.packs[0].id);
  $("packs").innerHTML = META.packs.map((p) => `
    <label class="pack"><input type="radio" name="pack" value="${esc(p.id)}" ${p.id === saved ? "checked" : ""}>
      <strong>${esc(p.title)}</strong><span>${esc(p.description)}</span></label>`).join("");
  $("lang").innerHTML = Object.entries(META.languages).map(([k, v]) => `<option value="${k}">${v === "Spanish" ? "Español" : v}</option>`).join("");
  const opts = (sel) => META.models.map((m) => `<option ${m === sel ? "selected" : ""}>${m}</option>`).join("");
  $("model").innerHTML = opts(META.settings.model);
  $("feedback_model").innerHTML = opts(META.settings.feedback_model);
  $("micmode").value = store.get("micMode", "hands-free");
  $("lang").value = store.get("lang", "en");
  const applyPack = () => {
    const p = META.packs.find((x) => x.id === document.querySelector("input[name=pack]:checked")?.value);
    if (!p) return;
    $("company").value = p.company || "";
    $("company").placeholder = p.company || "p. ej. Stripe, Airbnb…";
  };
  $("packs").onchange = applyPack;
  applyPack();
}

$("setup").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pack = document.querySelector("input[name=pack]:checked")?.value;
  if (!pack) return;
  $("startBtn").disabled = true;
  $("startBtn").textContent = "Preparando…";
  store.set("lastPack", pack);
  store.set("micMode", $("micmode").value);
  store.set("lang", $("lang").value);
  stopMicTest();
  try {
    await api("/api/settings", { model: $("model").value, feedback_model: $("feedback_model").value });
    META.settings.model = $("model").value;
    META.settings.feedback_model = $("feedback_model").value;
    const s = await api("/api/sessions", {
      pack, company: $("company").value.trim(), level: $("level").value, focus: $("focus").value.trim(),
      lang: $("lang").value, model: $("model").value, feedback_model: $("feedback_model").value,
    });
    autoEnableMic = true;   // this click counts as the user gesture for mic + audio
    location.hash = `#/s/${encodeURIComponent(s.meta.id)}`;
  } catch (err) {
    alert("No se pudo crear la sesión: " + err.message);
  } finally {
    $("startBtn").disabled = false;
    $("startBtn").textContent = "▶ Empezar entrevista";
  }
});

async function renderHistory() {
  const list = await api("/api/sessions");
  $("history").innerHTML = list.length ? list.map((s) => {
    const d = new Date(s.created * 1000);
    const when = d.toLocaleDateString("es", { day: "numeric", month: "short" }) + " · " + d.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
    const badge = s.verdict ? `<span class="badge ${VERDICTS[s.verdict] || ""}">${esc(s.verdict)}</span>`
      : `<span class="badge ${s.status === "live" ? "live" : ""}">${esc(STATUS_LABEL[s.status] || s.status)}</span>`;
    return `<li><a href="#/s/${encodeURIComponent(s.id)}">
      <strong>${esc(s.pack_title)}${s.company ? " · " + esc(s.company) : ""}</strong>${badge}
      <span class="when">${when}${s.minutes ? " · " + s.minutes + " min" : ""}</span></a></li>`;
  }).join("") : `<li class="empty">Aún no hay sesiones. ¡Empieza la primera!</li>`;
}

// mic test on the home screen
let testMic = null, testTimer = null;
function stopMicTest() { testMic?.disable(); testMic = null; clearTimeout(testTimer); $("micTestLevel").style.width = "0"; }
$("micTest").onclick = async () => {
  if (!Mic.supported()) { $("micTestMsg").textContent = "Este navegador no permite usar el micrófono aquí. Abre la app en Chrome o Safari."; return; }
  stopMicTest();
  testMic = new Mic({ mode: "ptt", onLevel: (l) => ($("micTestLevel").style.width = Math.min(100, l * 900) + "%") });
  try {
    await testMic.enable();
    $("micTestMsg").textContent = "Habla: la barra debería moverse.";
    testTimer = setTimeout(() => { stopMicTest(); $("micTestMsg").textContent = "Listo."; }, 8000);
  } catch (e) { $("micTestMsg").textContent = "Sin acceso al micrófono: " + e.message; }
};

// ====================================================================== session view
let S = null;            // { id, meta, msgs: Map, board, ... }
let autoEnableMic = false;
const mic = new Mic();
const player = makePlayer();

function leaveSession() {
  if (!S) return;
  S.board?.unmount();
  player.stop();
  mic.disable();
  clearInterval(S.tick);
  S = null;
}

async function openSession(id) {
  leaveSession();
  $("home").hidden = true;
  $("session").hidden = false;
  let data;
  try { data = await api(`/api/sessions/${encodeURIComponent(id)}`); }
  catch { alert("No encuentro esa sesión."); location.hash = ""; return; }

  S = { id, meta: data.meta, msgs: new Map(), feedback: data.feedback, server: "idle", detail: "", thinkingSince: 0, transcribing: false };
  document.title = `${data.meta.pack_title} · Mock Interview`;
  $("sTitle").textContent = data.meta.pack_title;
  $("sSub").textContent = [data.meta.company, data.meta.level, META.languages[data.meta.lang]].filter(Boolean).join(" · ");
  $("log").innerHTML = "";
  data.messages.forEach(upsert);
  scrollLog(true);
  applyMeta();
  showTab(isLive() ? "chat" : data.feedback ? "feedback" : "chat");

  // whiteboard
  let scene = null;
  try { const r = await fetch(`/files/${encodeURIComponent(id)}/canvas.excalidraw`); if (r.ok) scene = await r.json(); } catch {}
  S.board = new Board($("board"), { readOnly: !isLive(), theme: dark() ? "dark" : "light", onSync: syncBoard });
  S.board.mount(scene);

  // voice settings popover
  const prefix = data.meta.lang === "es" ? ["ef", "em"] : ["af", "am", "bf", "bm"];
  $("voice").innerHTML = META.voices.filter((v) => prefix.includes(v.slice(0, 2))).map((v) => `<option value="${v}">${voiceName(v)}</option>`).join("");
  $("voice").value = META.settings[`voice_${data.meta.lang}`] || "";
  $("speed").value = String(META.settings.speed);
  $("endSilence").value = store.get("endSilence", "1300");
  mic.endSilenceMs = +$("endSilence").value;
  mic.setMode(store.get("micMode", "hands-free"));

  if (!Mic.supported()) banner("Tu navegador no permite el micrófono aquí. Abre <b>http://127.0.0.1:8765</b> en Chrome o Safari para hablar; mientras, puedes escribir.");
  else banner("");
  if (isLive() && autoEnableMic && Mic.supported()) enableMic();
  autoEnableMic = false;
  renderMic();
  renderStatus();
  S.tick = setInterval(tick, 500);
}

const isLive = () => S?.meta.status === "live";

function voiceName(v) {
  const [p, n] = v.split("_");
  const acc = { a: "US", b: "UK", e: "ES" }[p[0]] || "";
  const g = p[1] === "f" ? "♀" : "♂";
  return `${n[0].toUpperCase() + n.slice(1)} (${acc} ${g})`;
}

function banner(html) { $("banner").innerHTML = html; $("banner").hidden = !html; }

function applyMeta() {
  const m = S.meta;
  const live = isLive();
  $("composer").hidden = !live;
  $("endBtn").hidden = !(live || m.status === "ended");
  $("endBtn").textContent = m.status === "ended" ? "Reintentar evaluación" : "Terminar y evaluar";
  $("feedbackTab").hidden = !(m.status === "evaluating" || m.status === "done" || m.status === "ended" || S.feedback);
  $("boardSync").textContent = live ? "El entrevistador ve los cambios de tu pizarra mientras hablas."
    : "Pizarra de la sesión (solo lectura).";
  renderPhases();
  renderFeedback();
}

function renderPhases() {
  const m = S.meta;
  const cur = m.phase?.key;
  const idx = m.phases.findIndex((p) => p.key === cur);
  $("phases").innerHTML = m.phases.map((p, i) => {
    const cls = p.key === cur && isLive() ? "current" : i < idx || !isLive() ? "done" : "";
    return `<li class="${cls}" data-key="${p.key}"><button ${!isLive() ? "disabled" : ""}>${esc(p.label)}<span class="t"></span></button></li>`;
  }).join("");
  $("phases").querySelectorAll("li").forEach((li) => {
    li.querySelector("button").onclick = async () => {
      if (!isLive() || li.classList.contains("current")) return;
      const label = m.phases.find((p) => p.key === li.dataset.key).label;
      if (await ask(`¿Pasar a la fase «${label}»? El entrevistador lo sabrá.`, "Sí, pasar")) {
        api(`/api/sessions/${S.id}/phase`, { key: li.dataset.key });
      }
    };
  });
}

function tick() {
  if (!S) return;
  const m = S.meta, now = Date.now() / 1000;
  if (m.started_at) $("clock").textContent = fmt((m.ended_at || now) - m.started_at);
  const li = $("phases").querySelector("li.current");
  if (li && m.phase) {
    const p = m.phases.find((x) => x.key === m.phase.key);
    const el = now - m.phase.started_at;
    li.querySelector(".t").textContent = `${fmt(el)}/${p.minutes}:00`;
    li.classList.toggle("over", el > p.minutes * 60);
  }
  if (S.server === "thinking" && S.thinkingSince && now * 1000 - S.thinkingSince > 60000) renderStatus();
}

// ---------------------------------------------------------------- messages
function upsert(m) {
  S.msgs.set(m.id, m);
  let el = document.querySelector(`[data-id="${m.id}"]`);
  if (m.role === "interviewer" && m.kind === "speech" && !m.text && m.done) { el?.remove(); return; } // silent turn
  const fresh = !el;
  if (fresh) { el = document.createElement("div"); el.dataset.id = m.id; }
  if (m.role === "event") {
    el.className = "divider";
    el.textContent = `Fase: ${m.text}`;
  } else if (m.kind === "note") {
    el.className = "msg note";
    el.innerHTML = `<div class="who">Nota del entrevistador</div><div class="text">${md(m.text)}</div>`;
  } else {
    const who = m.role === "interviewer" ? "Entrevistador" : m.kind === "look" ? "Tú · 👀 pizarra" : m.kind === "typed" ? "Tú · escrito" : "Tú";
    el.className = `msg ${m.role}${m.interrupted ? " interrupted" : ""}`;
    const replay = m.role === "interviewer" && m.audio && Object.keys(m.audio).length ? `<button class="replay" title="Escuchar">▶</button>` : "";
    const typing = m.role === "interviewer" && !m.done && isLive() ? " typing" : "";
    el.innerHTML = `<div class="who">${who}${replay}${m.interrupted ? " · interrumpido" : ""}</div><div class="text${typing}">${esc(m.text)}</div>`;
    el.querySelector(".replay")?.addEventListener("click", () => player.replay(m));
  }
  if (fresh) {
    $("log").append(el);
    scrollLog();
  }
}

function scrollLog(force) {
  const log = $("log");
  if (force || log.scrollHeight - log.scrollTop - log.clientHeight < 160) log.scrollTop = log.scrollHeight;
}

// ---------------------------------------------------------------- audio playback
function makePlayer() {
  let queue = [], current = null, blocked = null;
  const p = {
    get playing() { return !!current; },
    get current() { return current; },
    enqueue(ev) { queue.push(ev); if (!current) next(); },
    stop() { queue = []; blocked = null; if (current) { const a = current.audio; current = null; a.pause(); } mark(); renderStatus(); },
    replay(m) {
      p.stop();
      Object.entries(m.audio || {}).sort((a, b) => a[0] - b[0]).forEach(([index, url]) => queue.push({ message_id: m.id, index: +index, url }));
      next();
    },
    resume() { if (blocked && !current) { queue.unshift(blocked); blocked = null; next(); } },
  };
  function mark() {
    document.querySelectorAll(".msg.speaking").forEach((e) => e.classList.remove("speaking"));
    if (current) document.querySelector(`[data-id="${current.message_id}"]`)?.classList.add("speaking");
  }
  function next() {
    const ev = queue.shift();
    if (!ev) { current = null; mark(); renderStatus(); return; }
    const audio = new Audio(ev.url);
    current = { ...ev, audio };
    mark(); renderStatus();
    let done = false;
    const go = () => { if (done || current?.audio !== audio) return; done = true; current = null; next(); };
    audio.onended = go;
    audio.onerror = go;
    audio.play().catch((e) => {
      if (e.name !== "NotAllowedError") return go();
      done = true; current = null; blocked = ev; mark();
      setStatus("Haz clic en cualquier sitio para activar el audio.", "err");
    });
  }
  document.addEventListener("click", () => p.resume());
  return p;
}

// ---------------------------------------------------------------- status line
function setStatus(text, dot = "") { $("statusText").textContent = text; $("dot").className = "dot " + dot; }

function renderStatus() {
  if (!S || !isLive()) {
    if (S?.meta.status === "evaluating") setStatus("Generando tu evaluación…", "think");
    return;
  }
  const tools = { Read: "Mirando tu pizarra…", WebSearch: "Investigando…", WebFetch: "Investigando…", Write: "Tomando notas…", Edit: "Tomando notas…" };
  if (mic.state === "speaking") return setStatus("Te escucho…", "live");
  if (S.transcribing) return setStatus("Transcribiendo…", "think");
  if (player.playing) return setStatus("El entrevistador está hablando — puedes interrumpirle.", "ok");
  switch (S.server) {
    case "preparing": return setStatus("Preparando la entrevista…", "think");
    case "tool": return setStatus(tools[S.detail] || "Pensando…", "think");
    case "thinking": {
      const slow = S.thinkingSince && Date.now() - S.thinkingSince > 60000;
      return setStatus(slow ? "Está tardando más de lo normal… (puedes seguir hablando)" : "El entrevistador está pensando…", "think");
    }
    case "error": return setStatus(S.detail || "Algo falló.", "err");
  }
  if (mic.state === "off") return setStatus("Tu turno. Activa el micrófono o escribe.", "");
  if (mic.state === "muted") return setStatus("Micrófono en silencio. Tu turno.", "");
  setStatus(mic.mode === "ptt" ? "Tu turno — pulsa el micrófono para hablar." : "Tu turno — habla cuando quieras.", "ok");
}

// ---------------------------------------------------------------- microphone
mic.isPlaybackActive = () => player.playing;
mic.onLevel = (l) => {
  const ring = $("micRing");
  ring.style.opacity = mic.state === "listening" || mic.state === "speaking" ? Math.min(1, l * 40) : 0;
  ring.style.transform = `scale(${1 + Math.min(0.25, l * 6)})`;
};
mic.onState = () => { renderMic(); renderStatus(); };
mic.onSpeechStart = () => {
  if (!S) return;
  const cur = player.current;
  if (cur) {
    player.stop();
    api(`/api/sessions/${S.id}/interrupt`, { message_id: cur.message_id, heard_index: cur.index }).catch(() => {});
  }
  api(`/api/sessions/${S.id}/activity`, { speaking: true }).catch(() => {});
};
mic.onUtterance = async (wav) => {
  if (!S) return;
  const sid = S.id;
  api(`/api/sessions/${sid}/activity`, { speaking: false }).catch(() => {});
  S.transcribing = true; renderStatus();
  try {
    const fd = new FormData();
    fd.append("audio", wav, "turn.wav");
    const r = await fetch(`/api/sessions/${sid}/transcribe`, { method: "POST", body: fd }).then((r) => r.json());
    if (!S || S.id !== sid) return;
    if (!r.text) { S.transcribing = false; setStatus("No te he entendido bien — repítelo, por favor.", "err"); return; }
    await S.board?.sync();  // the interviewer gets the latest drawing with what you said
    await api(`/api/sessions/${sid}/say`, { text: r.text });
  } catch (e) {
    setStatus("Error al transcribir: " + e.message, "err");
  } finally {
    if (S) { S.transcribing = false; renderStatus(); }
  }
};

async function enableMic() {
  try { await mic.enable(); banner(""); }
  catch (e) { banner("Sin acceso al micrófono (" + esc(e.message) + "). Revisa los permisos del navegador o escribe abajo."); }
}

function renderMic() {
  const b = $("mic");
  b.className = "mic" + ({ listening: " on", ready: " on", speaking: " speaking", muted: " muted" }[mic.state] || "");
  const T = {
    off: ["Micrófono apagado", "Pulsa para activarlo."],
    listening: ["Escuchando", "Habla con naturalidad; tu turno termina tras una pausa. Pulsa para silenciar."],
    ready: ["Listo", "Pulsa (o Ctrl+M) para hablar y otra vez para terminar."],
    speaking: mic.mode === "ptt" ? ["Grabando…", "Pulsa para terminar tu turno."] : ["Te escucho…", "Haz una pausa para terminar tu turno."],
    muted: ["Silenciado", "Pulsa para volver a escuchar."],
  }[mic.state] || ["", ""];
  $("micTitle").textContent = T[0];
  $("micHint").textContent = T[1];
}

async function micAction() {
  if (!isLive()) return;
  if (mic.state === "off") return enableMic();
  if (mic.mode === "ptt") return mic.state === "speaking" ? mic.stopTalking() : mic.startTalking();
  mic.setMuted(mic.state !== "muted");
}
$("mic").onclick = micAction;
window.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.key.toLowerCase() === "m" && S) { e.preventDefault(); e.stopPropagation(); micAction(); }
}, true);

$("typeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("typeInput").value.trim();
  if (!text || !S) return;
  $("typeInput").value = "";
  await S.board?.sync();
  api(`/api/sessions/${S.id}/say`, { text, kind: "typed" });
});

$("lookBtn").onclick = async () => {
  if (!S) return;
  const text = $("typeInput").value.trim();
  $("typeInput").value = "";
  $("boardSync").textContent = "Enviando tu pizarra…";
  await S.board?.sync();
  api(`/api/sessions/${S.id}/look`, { text });
};

async function syncBoard(payload) {
  if (!S || !isLive()) return;
  await api(`/api/sessions/${S.id}/canvas`, payload);
  $("boardSync").textContent = `El entrevistador tiene tu pizarra · actualizada ${new Date().toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
}

// ---------------------------------------------------------------- header actions
$("back").onclick = async () => {
  if (isLive() && !(await ask("La entrevista sigue en curso. Podrás volver a ella desde el historial.", "Salir"))) return;
  location.hash = "";
};
$("endBtn").onclick = async () => {
  if (S.meta.status === "live" && !(await ask("¿Terminar la entrevista y generar la evaluación?", "Terminar"))) return;
  player.stop();
  mic.disable();
  api(`/api/sessions/${S.id}/end`, {});
};
$("settingsBtn").onclick = () => ($("settings").hidden = !$("settings").hidden);
document.addEventListener("click", (e) => {
  if (!$("settings").hidden && !e.target.closest(".settings-wrap")) $("settings").hidden = true;
});
$("voice").onchange = () => {
  const key = `voice_${S.meta.lang}`;
  META.settings[key] = $("voice").value;
  api("/api/settings", { [key]: $("voice").value });
};
$("speed").onchange = () => { META.settings.speed = +$("speed").value; api("/api/settings", { speed: +$("speed").value }); };
$("endSilence").onchange = () => { mic.endSilenceMs = +$("endSilence").value; store.set("endSilence", $("endSilence").value); };

// ---------------------------------------------------------------- tabs + feedback
function showTab(name) {
  $("tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("chatTab").hidden = name !== "chat";
  $("fbTab").hidden = name !== "feedback";
}
$("tabs").addEventListener("click", (e) => { const t = e.target.closest("button")?.dataset.tab; if (t) showTab(t); });

function renderFeedback() {
  const box = $("fbTab");
  const st = S.meta.status;
  if (st === "evaluating") {
    box.innerHTML = `<div class="evaluating"><span class="dot think"></span><strong>Generando tu evaluación…</strong>
      <span class="small">El modelo ${esc(S.meta.feedback_model)} está revisando el transcript y tus bocetos. Suele tardar 1–3 minutos.</span></div>`;
    return;
  }
  const f = S.feedback;
  if (!f) {
    box.innerHTML = st === "ended" ? `<div class="evaluating">No hay evaluación todavía. Usa «Reintentar evaluación» arriba.</div>` : "";
    return;
  }
  const rubric = S.meta.rubric || Object.keys(f.scores || {});
  box.innerHTML = `<div class="fb">
    <div class="verdict"><span class="badge ${VERDICTS[f.verdict] || ""}">${esc(f.verdict || "—")}</span>
      <span class="small muted">evaluado por ${esc(S.meta.feedback_model)}</span></div>
    <p>${esc(f.summary || "")}</p>
    ${f.scores ? `<h3>Puntuación</h3><div class="scores">${rubric.filter((k) => f.scores[k] != null).map((k) => `
      <div class="score"><span>${esc(k)}</span><div class="bar5"><div style="width:${f.scores[k] * 20}%"></div></div><b>${f.scores[k]}</b></div>`).join("")}</div>` : ""}
    ${f.top3?.length ? `<h3>Top 3 para practicar</h3><ol class="top3">${f.top3.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>` : ""}
    ${f.review?.length ? `<h3>Para revisar</h3><ul class="review">${f.review.map((r) => `
      <li data-ref="${r.id}"><div class="topic">${esc(r.topic)}</div><div class="small">${esc(r.what)}</div>
      <div class="ref">ver momento #${r.id} →</div></li>`).join("")}</ul>` : ""}
    <div class="report">${md(f.report)}</div></div>`;
  box.querySelectorAll(".review li").forEach((li) => li.onclick = () => jumpTo(+li.dataset.ref));
}

function jumpTo(id) {
  showTab("chat");
  const el = document.querySelector(`[data-id="${id}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}

// ====================================================================== live events
const es = new EventSource("/api/events");
es.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === "settings") { META.settings = ev.settings; return; }
  if (!S || ev.session !== S.id) return;
  switch (ev.type) {
    case "message":
      upsert(ev.message);
      break;
    case "audio": {
      const m = S.msgs.get(ev.message_id);
      if (m) { m.audio = { ...(m.audio || {}), [ev.index]: ev.url }; upsert(m); }
      if (isLive() && !m?.interrupted && mic.state !== "speaking") player.enqueue(ev);
      break;
    }
    case "status":
      S.server = ev.state;
      S.detail = ev.detail;
      if (ev.state === "thinking" && !S.thinkingSince) S.thinkingSince = Date.now();
      if (ev.state !== "thinking" && ev.state !== "tool") S.thinkingSince = 0;
      renderStatus();
      break;
    case "meta": {
      const wasLive = isLive();
      S.meta = ev.meta;
      applyMeta();
      if (wasLive && !isLive()) { mic.disable(); showTab("feedback"); }
      renderStatus();
      break;
    }
    case "feedback":
      S.feedback = ev.feedback;
      applyMeta();
      showTab("feedback");
      break;
  }
};

route();
