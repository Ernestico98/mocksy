import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import { api, subscribe, type AppMeta, type Feedback, type Message, type SessionMeta } from "../api";
import { useConfirm } from "../components/Confirm";
import FeedbackPanel from "../components/FeedbackPanel";
import Transcript from "../components/Transcript";
import Whiteboard, { type WhiteboardHandle } from "../components/Whiteboard";
import { Mic, type MicMode, type MicState } from "../lib/audio";
import { player } from "../lib/player";
import { fmt, go, store, voiceName } from "../lib/util";
import { startIntent } from "./Home";

const TOOL_STATUS: Record<string, string> = {
  Read: "Mirando tu pizarra…", WebSearch: "Investigando…", WebFetch: "Investigando…",
  Write: "Tomando notas…", Edit: "Tomando notas…",
};

interface Server { state: string; detail: string; since: number }

function useNow(ms: number) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export default function SessionView({ id, meta: app }: { id: string; meta: AppMeta }) {
  const ask = useConfirm();
  const [meta, setMeta] = useState<SessionMeta | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [server, setServer] = useState<Server>({ state: "idle", detail: "", since: 0 });
  const [tab, setTab] = useState<"chat" | "feedback">("chat");
  const [flashId, setFlashId] = useState<number | null>(null);
  const [micState, setMicState] = useState<MicState>("off");
  const [level, setLevel] = useState(0);
  const [transcribing, setTranscribing] = useState(false);
  const [notice, setNotice] = useState("");
  const [micError, setMicError] = useState("");
  const [synced, setSynced] = useState<Date | null>(null);
  const [error, setError] = useState("");

  const board = useRef<WhiteboardHandle>(null);
  const mic = useRef<Mic>(null as unknown as Mic);
  if (!mic.current) mic.current = new Mic();
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const metaRef = useRef(meta);
  metaRef.current = meta;

  const current = useSyncExternalStore((cb) => player.subscribe(cb), () => player.current);
  const needsGesture = useSyncExternalStore((cb) => player.subscribe(cb), () => player.needsGesture);
  const live = meta?.status === "live";

  const upsert = useCallback((m: Message) => {
    setMessages((list) => {
      const i = list.findIndex((x) => x.id === m.id);
      if (i === -1) return [...list, m];
      const next = list.slice();
      next[i] = { ...list[i], ...m };
      return next;
    });
  }, []);

  // ---------------------------------------------------------------- load + live events
  useEffect(() => {
    api.session(id).then((d) => {
      setMeta(d.meta);
      setMessages(d.messages);
      setFeedback(d.feedback);
      setTab(d.meta.status !== "live" && d.feedback ? "feedback" : "chat");
      document.title = `${d.meta.pack_title} · Mocksy`;
    }).catch(() => setError("No encuentro esa sesión."));

    const off = subscribe((ev) => {
      if (!("session" in ev) || ev.session !== id) return;
      switch (ev.type) {
        case "message":
          upsert(ev.message);
          break;
        case "audio": {
          const m = messagesRef.current.find((x) => x.id === ev.message_id);
          if (m) upsert({ ...m, audio: { ...(m.audio || {}), [ev.index]: ev.url } });
          if (metaRef.current?.status === "live" && !m?.interrupted && mic.current.state !== "speaking") {
            player.enqueue({ message_id: ev.message_id, index: ev.index, url: ev.url });
          }
          break;
        }
        case "status":
          setServer((s) => ({
            state: ev.state,
            detail: ev.detail || "",
            since: ev.state === "thinking" || ev.state === "tool" ? s.since || Date.now() : 0,
          }));
          break;
        case "meta":
          setMeta((prev) => {
            if (prev?.status === "live" && ev.meta.status !== "live") {
              mic.current.disable();
              setTab("feedback");
            }
            return ev.meta;
          });
          break;
        case "feedback":
          setFeedback(ev.feedback);
          setTab("feedback");
          break;
      }
    });
    return () => {
      off();
      player.stop();
      mic.current.disable();
    };
  }, [id, upsert]);

  // ---------------------------------------------------------------- microphone wiring
  useEffect(() => {
    const m = mic.current;
    m.setMode(store.get("micMode", "hands-free") as MicMode);
    m.endSilenceMs = +store.get("endSilence", "1300");
    m.isPlaybackActive = () => player.playing;
    m.onState = setMicState;
    m.onLevel = setLevel;
    m.onSpeechStart = () => {
      setNotice("");
      const clip = player.current;
      if (clip) {   // barge-in: stop talking and tell the interviewer what was heard
        player.stop();
        void api.interrupt(id, clip.message_id, clip.index).catch(() => {});
      }
      void api.activity(id, true).catch(() => {});
    };
    m.onUtterance = async (wav) => {
      void api.activity(id, false).catch(() => {});
      setTranscribing(true);
      try {
        const text = await api.transcribe(id, wav);
        if (!text) { setNotice("No te he entendido bien — repítelo, por favor."); return; }
        await board.current?.sync();  // the interviewer gets the latest drawing with what you said
        await api.say(id, text);
      } catch (e) {
        setNotice("Error al transcribir: " + (e as Error).message);
      } finally {
        setTranscribing(false);
      }
    };
  }, [id]);

  const enableMic = useCallback(async () => {
    try { await mic.current.enable(); setMicError(""); }
    catch (e) { setMicError(`Sin acceso al micrófono (${(e as Error).message}). Revisa los permisos o escribe abajo.`); }
  }, []);

  // the "Start" click on the home screen is the user gesture that unlocks mic + audio
  useEffect(() => {
    if (live && startIntent.autoEnableMic && Mic.supported()) void enableMic();
    if (meta) startIntent.autoEnableMic = false;
  }, [live, meta, enableMic]);

  const micAction = useCallback(() => {
    const m = mic.current;
    if (metaRef.current?.status !== "live") return;
    if (m.state === "off") return void enableMic();
    if (m.mode === "ptt") return m.state === "speaking" ? m.stopTalking() : m.startTalking();
    m.setMuted(m.state !== "muted");
  }, [enableMic]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key.toLowerCase() === "m") { e.preventDefault(); e.stopPropagation(); micAction(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [micAction]);

  // ---------------------------------------------------------------- actions
  const [typed, setTyped] = useState("");

  async function sendTyped(e: FormEvent) {
    e.preventDefault();
    const text = typed.trim();
    if (!text) return;
    setTyped("");
    await board.current?.sync();
    await api.say(id, text, "typed");
  }

  async function askToLook() {
    const text = typed.trim();
    setTyped("");
    await board.current?.sync();
    await api.look(id, text);
  }

  async function changePhase(key: string, label: string) {
    if (await ask(`¿Pasar a la fase «${label}»? El entrevistador lo sabrá.`, "Sí, pasar")) await api.phase(id, key);
  }

  async function end() {
    if (live && !(await ask("¿Terminar la entrevista y generar la evaluación?", "Terminar"))) return;
    player.stop();
    mic.current.disable();
    await api.end(id);
  }

  async function back() {
    if (live && !(await ask("La entrevista sigue en curso. Podrás volver desde el historial.", "Salir"))) return;
    go();
  }

  const jumpTo = (messageId: number) => {
    setTab("chat");
    setFlashId(null);
    requestAnimationFrame(() => setFlashId(messageId));
  };

  // ---------------------------------------------------------------- render
  if (error) return <div className="boot">{error} <a href="#/">Volver</a></div>;
  if (!meta) return <div className="boot">Cargando sesión…</div>;

  const status = statusLine({ live, server, micState, mode: mic.current.mode, transcribing, playing: !!current, notice, needsGesture, evaluating: meta.status === "evaluating" });
  const showFeedbackTab = meta.status !== "live" || !!feedback;

  return (
    <div className="session">
      <Header
        meta={meta} app={app} live={live} onBack={back} onEnd={end} onPhase={changePhase}
        onEndSilence={(ms) => { mic.current.endSilenceMs = ms; store.set("endSilence", String(ms)); }}
      />
      <main className="workspace">
        <aside className="side">
          <nav className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "chat"} className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>Conversación</button>
            {showFeedbackTab && (
              <button role="tab" aria-selected={tab === "feedback"} className={tab === "feedback" ? "active" : ""} onClick={() => setTab("feedback")}>Evaluación</button>
            )}
          </nav>

          {tab === "chat" ? (
            <div className="tab-body">
              {live && !Mic.supported() && (
                <div className="banner">Este navegador no permite el micrófono aquí. Abre <b>{location.origin}</b> en Chrome o Safari para hablar; mientras, puedes escribir.</div>
              )}
              {micError && <div className="banner">{micError}</div>}
              <Transcript messages={messages} live={live} speakingId={current?.message_id ?? null} flashId={flashId} />
              {live && (
                <div className="composer">
                  <div className="status-line"><span className={`dot ${status.dot}`} /><span>{status.text}</span></div>
                  <div className="mic-row">
                    <MicButton state={micState} level={level} onClick={micAction} />
                    <MicHelp state={micState} mode={mic.current.mode} />
                    <button className="btn ghost" onClick={askToLook} title="Pide al entrevistador que mire tu pizarra ahora">👀 Mira mi pizarra</button>
                  </div>
                  <form className="type-form" onSubmit={sendTyped}>
                    <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="…o escribe aquí y pulsa Enter" autoComplete="off" />
                  </form>
                </div>
              )}
            </div>
          ) : (
            <div className="tab-body scroll"><FeedbackPanel meta={meta} feedback={feedback} onJump={jumpTo} /></div>
          )}
        </aside>

        <section className="board-wrap">
          <div className="board-bar small muted">
            {!live ? "Pizarra de la sesión (solo lectura)."
              : synced ? `El entrevistador tiene tu pizarra · actualizada ${synced.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
              : "Dibuja aquí: el entrevistador ve los cambios mientras hablas."}
          </div>
          <div className="board">
            <Whiteboard ref={board} sessionId={id} readOnly={!live} onSynced={setSynced} />
          </div>
        </section>
      </main>
    </div>
  );
}

// ------------------------------------------------------------------ header

function Header({ meta, app, live, onBack, onEnd, onPhase, onEndSilence }: {
  meta: SessionMeta; app: AppMeta; live: boolean;
  onBack: () => void; onEnd: () => void; onPhase: (key: string, label: string) => void; onEndSilence: (ms: number) => void;
}) {
  const now = useNow(500);
  const [open, setOpen] = useState(false);
  const idx = meta.phases.findIndex((p) => p.key === meta.phase?.key);
  const total = meta.started_at ? (meta.ended_at || now) - meta.started_at : 0;

  return (
    <header className="bar">
      <button className="icon-btn" onClick={onBack} title="Volver al inicio" aria-label="Volver al inicio">←</button>
      <div className="title">
        <strong>{meta.pack_title}</strong>
        <span className="muted small">{[meta.company, meta.level, app.languages[meta.lang]].filter(Boolean).join(" · ")}</span>
      </div>
      <ol className="phases">
        {meta.phases.map((p, i) => {
          const isCur = live && i === idx;
          const elapsed = isCur && meta.phase ? now - meta.phase.started_at : 0;
          const cls = [isCur && "current", isCur && elapsed > p.minutes * 60 && "over", (i < idx || !live) && "done"].filter(Boolean).join(" ");
          return (
            <li key={p.key} className={cls}>
              <button disabled={!live || isCur} onClick={() => onPhase(p.key, p.label)} title={isCur ? "Fase actual" : `Pasar a ${p.label}`}>
                {p.label}{isCur && <span className="t">{fmt(elapsed)}/{p.minutes}:00</span>}
              </button>
            </li>
          );
        })}
      </ol>
      <span className="clock" title="Tiempo total">{fmt(total)}</span>
      <span className="spacer" />
      {live && (
        <div className="settings-wrap">
          <button className="icon-btn" onClick={() => setOpen((o) => !o)} title="Ajustes de voz" aria-expanded={open}>⚙</button>
          {open && <VoiceSettings meta={meta} app={app} onEndSilence={onEndSilence} onClose={() => setOpen(false)} />}
        </div>
      )}
      {(live || meta.status === "ended") && (
        <button className="btn danger" onClick={onEnd}>{meta.status === "ended" ? "Reintentar evaluación" : "Terminar y evaluar"}</button>
      )}
    </header>
  );
}

function VoiceSettings({ meta, app, onEndSilence, onClose }: {
  meta: SessionMeta; app: AppMeta; onEndSilence: (ms: number) => void; onClose: () => void;
}) {
  const key = meta.lang === "es" ? "voice_es" : "voice_en";
  const prefixes = meta.lang === "es" ? ["ef", "em"] : ["af", "am", "bf", "bm"];
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (!ref.current?.parentElement?.contains(e.target as Node)) onClose(); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [onClose]);

  return (
    <div className="popover" ref={ref}>
      <label className="field"><span>Voz</span>
        <select defaultValue={app.settings[key]} onChange={(e) => void api.saveSettings({ [key]: e.target.value })}>
          {app.voices.filter((v) => prefixes.includes(v.slice(0, 2))).map((v) => <option key={v} value={v}>{voiceName(v)}</option>)}
        </select>
      </label>
      <label className="field"><span>Velocidad</span>
        <select defaultValue={String(app.settings.speed)} onChange={(e) => void api.saveSettings({ speed: +e.target.value })}>
          {[0.9, 1, 1.1, 1.2].map((s) => <option key={s} value={String(s)}>{s.toFixed(1)}×</option>)}
        </select>
      </label>
      <label className="field"><span>Pausa para terminar tu turno</span>
        <select defaultValue={store.get("endSilence", "1300")} onChange={(e) => onEndSilence(+e.target.value)}>
          <option value="900">Corta (0,9 s)</option>
          <option value="1300">Normal (1,3 s)</option>
          <option value="2000">Larga (2 s)</option>
          <option value="3000">Muy larga (3 s)</option>
        </select>
      </label>
    </div>
  );
}

// ------------------------------------------------------------------ mic UI

function MicButton({ state, level, onClick }: { state: MicState; level: number; onClick: () => void }) {
  const cls = { listening: "on", ready: "on", speaking: "speaking", muted: "muted", off: "" }[state];
  const active = state === "listening" || state === "speaking";
  return (
    <button className={`mic ${cls}`} onClick={onClick} aria-label="Micrófono" title="Micrófono (Ctrl+M)">
      <span aria-hidden>🎙</span>
      <span className="mic-ring" style={{ opacity: active ? Math.min(1, level * 40) : 0, transform: `scale(${1 + Math.min(0.25, level * 6)})` }} />
    </button>
  );
}

function MicHelp({ state, mode }: { state: MicState; mode: MicMode }) {
  const [title, hint] = {
    off: ["Micrófono apagado", "Pulsa para activarlo."],
    listening: ["Escuchando", "Habla con naturalidad; tu turno termina tras una pausa. Pulsa para silenciar."],
    ready: ["Listo", "Pulsa (o Ctrl+M) para hablar, y otra vez para terminar."],
    speaking: mode === "ptt" ? ["Grabando…", "Pulsa para terminar tu turno."] : ["Te escucho…", "Haz una pausa para terminar tu turno."],
    muted: ["Silenciado", "Pulsa para volver a escuchar."],
  }[state];
  return (
    <div className="mic-help">
      <strong>{title}</strong>
      <span className="muted small">{hint}</span>
    </div>
  );
}

function statusLine(s: {
  live: boolean; server: Server; micState: MicState; mode: MicMode; transcribing: boolean;
  playing: boolean; notice: string; needsGesture: boolean; evaluating: boolean;
}): { text: string; dot: string } {
  if (s.evaluating) return { text: "Generando tu evaluación…", dot: "think" };
  if (!s.live) return { text: "", dot: "" };
  if (s.micState === "speaking") return { text: "Te escucho…", dot: "live" };
  if (s.transcribing) return { text: "Transcribiendo…", dot: "think" };
  if (s.needsGesture) return { text: "Haz clic en cualquier sitio para activar el audio.", dot: "err" };
  if (s.playing) return { text: "El entrevistador está hablando — puedes interrumpirle.", dot: "ok" };
  if (s.notice) return { text: s.notice, dot: "err" };
  switch (s.server.state) {
    case "preparing": return { text: "Preparando la entrevista…", dot: "think" };
    case "tool": return { text: TOOL_STATUS[s.server.detail] || "Pensando…", dot: "think" };
    case "thinking": {
      const slow = s.server.since && Date.now() - s.server.since > 60000;
      return { text: slow ? "Está tardando más de lo normal… (puedes seguir hablando)" : "El entrevistador está pensando…", dot: "think" };
    }
    case "error": return { text: s.server.detail || "Algo falló.", dot: "err" };
  }
  if (s.micState === "off") return { text: "Tu turno. Activa el micrófono o escribe.", dot: "" };
  if (s.micState === "muted") return { text: "Micrófono en silencio. Tu turno.", dot: "" };
  return { text: s.mode === "ptt" ? "Tu turno — pulsa el micrófono para hablar." : "Tu turno — habla cuando quieras.", dot: "ok" };
}
