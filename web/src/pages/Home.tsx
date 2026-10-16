import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type AppMeta, type SessionSummary } from "../api";
import { Mic, type MicMode } from "../lib/audio";
import { STATUS_LABEL, VERDICT_TONE, go, store } from "../lib/util";

const LEVELS = ["Junior", "Mid", "Senior", "Staff"];

/** Set by the start button: that click is the gesture that unlocks mic + audio on the next screen. */
export const startIntent = { autoEnableMic: false };

export default function Home({ meta }: { meta: AppMeta }) {
  const [pack, setPack] = useState(() => {
    const saved = store.get("lastPack", "");
    return meta.packs.some((p) => p.id === saved) ? saved : meta.packs[0].id;
  });
  const current = meta.packs.find((p) => p.id === pack)!;
  const [company, setCompany] = useState(current.company);
  const [level, setLevel] = useState(store.get("level", "Mid"));
  const [focus, setFocus] = useState("");
  const [lang, setLang] = useState(store.get("lang", "en"));
  const [micMode, setMicMode] = useState<MicMode>(store.get("micMode", "hands-free") as MicMode);
  const [model, setModel] = useState(meta.settings.model);
  const [feedbackModel, setFeedbackModel] = useState(meta.settings.feedback_model);
  const [starting, setStarting] = useState(false);
  const [history, setHistory] = useState<SessionSummary[] | null>(null);

  useEffect(() => { document.title = "Mocksy"; api.sessions().then(setHistory); }, []);

  const choosePack = (id: string) => {
    setPack(id);
    setCompany(meta.packs.find((p) => p.id === id)?.company || "");
  };

  async function start(e: FormEvent) {
    e.preventDefault();
    setStarting(true);
    store.set("lastPack", pack);
    store.set("level", level);
    store.set("lang", lang);
    store.set("micMode", micMode);
    try {
      await api.saveSettings({ model, feedback_model: feedbackModel });
      const s = await api.createSession({ pack, company: company.trim(), level, focus: focus.trim(), lang, model, feedback_model: feedbackModel });
      startIntent.autoEnableMic = true;
      go(s.meta.id);
    } catch (err) {
      alert("No se pudo crear la sesión: " + (err as Error).message);
      setStarting(false);
    }
  }

  return (
    <div className="home">
      <header className="home-head">
        <h1 className="brand">Mocksy</h1>
        <p className="muted">Ensaya entrevistas técnicas en voz alta, con pizarra y un entrevistador que te evalúa al final.</p>
      </header>

      <div className="home-grid">
        <form className="card setup" onSubmit={start}>
          <h2>Nueva entrevista</h2>

          <fieldset className="field">
            <legend>Tipo de entrevista</legend>
            <div className="packs">
              {meta.packs.map((p) => (
                <label key={p.id} className="pack">
                  <input type="radio" name="pack" checked={p.id === pack} onChange={() => choosePack(p.id)} />
                  <strong>{p.title}</strong>
                  <span>{p.description}</span>
                  <span className="phase-pills">{p.phases.map((ph) => `${ph.label} ${ph.minutes}′`).join(" · ")}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="row2">
            <label className="field">
              <span>Empresa <span className="muted">(opcional)</span></span>
              <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="p. ej. Stripe, Airbnb…" autoComplete="off" />
            </label>
            <label className="field">
              <span>Nivel</span>
              <select value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="">Sin especificar</option>
                {LEVELS.map((l) => <option key={l}>{l}</option>)}
              </select>
            </label>
          </div>

          <label className="field">
            <span>Enfoque <span className="muted">(opcional)</span></span>
            <input value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="p. ej. presióname en modos de fallo y costes" autoComplete="off" />
          </label>

          <div className="row2">
            <label className="field">
              <span>Idioma de la entrevista</span>
              <select value={lang} onChange={(e) => setLang(e.target.value)}>
                {Object.entries(meta.languages).map(([k, v]) => <option key={k} value={k}>{v === "Spanish" ? "Español" : v}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Micrófono</span>
              <select value={micMode} onChange={(e) => setMicMode(e.target.value as MicMode)}>
                <option value="hands-free">Manos libres (detecta cuándo hablas)</option>
                <option value="ptt">Pulsar para hablar</option>
              </select>
            </label>
          </div>

          <details className="advanced">
            <summary>Modelos</summary>
            <div className="row2">
              <label className="field">
                <span>Entrevistador</span>
                <select value={model} onChange={(e) => setModel(e.target.value)}>
                  {meta.models.map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Evaluación final</span>
                <select value={feedbackModel} onChange={(e) => setFeedbackModel(e.target.value)}>
                  {meta.models.map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
            </div>
            <p className="hint">Se ejecutan con tu sesión de Claude Code. Para conversar conviene un modelo rápido; para evaluar, uno más profundo.</p>
          </details>

          <MicCheck />

          <button type="submit" className="btn primary big" disabled={starting}>
            {starting ? "Preparando…" : "▶ Empezar entrevista"}
          </button>
        </form>

        <div className="card history">
          <h2>Sesiones anteriores</h2>
          <ul className="history-list">
            {history === null ? <li className="empty">Cargando…</li>
              : history.length === 0 ? <li className="empty">Aún no hay sesiones. ¡Empieza la primera!</li>
              : history.map((s) => <HistoryItem key={s.id} s={s} />)}
          </ul>
        </div>
      </div>
    </div>
  );
}

function HistoryItem({ s }: { s: SessionSummary }) {
  const d = new Date(s.created * 1000);
  const when = d.toLocaleDateString("es", { day: "numeric", month: "short" }) + " · " +
    d.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
  const badge = s.verdict
    ? <span className={`badge ${VERDICT_TONE[s.verdict] || ""}`}>{s.verdict}</span>
    : <span className={`badge ${s.status === "live" ? "live" : ""}`}>{STATUS_LABEL[s.status] || s.status}</span>;
  return (
    <li>
      <a href={`#/s/${encodeURIComponent(s.id)}`}>
        <strong>{s.pack_title}{s.company ? ` · ${s.company}` : ""}</strong>
        {badge}
        <span className="when">{when}{s.minutes ? ` · ${s.minutes} min` : ""}</span>
      </a>
    </li>
  );
}

function MicCheck() {
  const [level, setLevel] = useState(0);
  const [msg, setMsg] = useState("");
  const mic = useRef<Mic | null>(null);
  const timer = useRef<number>(0);

  const stop = () => {
    mic.current?.disable();
    mic.current = null;
    clearTimeout(timer.current);
    setLevel(0);
  };
  useEffect(() => stop, []);

  async function test() {
    if (!Mic.supported()) {
      setMsg("Este navegador no permite el micrófono aquí. Abre la app en Chrome o Safari.");
      return;
    }
    stop();
    const m = new Mic({ mode: "ptt", onLevel: setLevel });
    mic.current = m;
    try {
      await m.enable();
      setMsg("Habla: la barra debería moverse.");
      timer.current = window.setTimeout(() => { stop(); setMsg("Listo."); }, 8000);
    } catch (e) {
      setMsg("Sin acceso al micrófono: " + (e as Error).message);
    }
  }

  return (
    <div className="mic-check">
      <button type="button" className="btn" onClick={test}>🎙 Probar micrófono</button>
      <div className="meter"><div style={{ width: `${Math.min(100, level * 900)}%` }} /></div>
      <span className="muted small">{msg}</span>
    </div>
  );
}
