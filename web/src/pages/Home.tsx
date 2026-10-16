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
  const [problem, setProblem] = useState("");
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
      const s = await api.createSession({ pack, company: company.trim(), level, focus: focus.trim(), problem: problem.trim(), lang, model, feedback_model: feedbackModel });
      startIntent.autoEnableMic = true;
      go(s.meta.id);
    } catch (err) {
      alert("Couldn't create the session: " + (err as Error).message);
      setStarting(false);
    }
  }

  return (
    <div className="home">
      <header className="home-head">
        <h1 className="brand">Mocksy</h1>
        <p className="muted">Rehearse technical interviews out loud, with a whiteboard and an interviewer who evaluates you at the end.</p>
      </header>

      <div className="home-grid">
        <form className="card setup" onSubmit={start}>
          <h2>New interview</h2>

          <fieldset className="field">
            <legend>Interview type</legend>
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
              <span>Company <span className="muted">(optional)</span></span>
              <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="e.g. Stripe, Airbnb…" autoComplete="off" />
            </label>
            <label className="field">
              <span>Level</span>
              <select value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="">Not specified</option>
                {LEVELS.map((l) => <option key={l}>{l}</option>)}
              </select>
            </label>
          </div>

          <label className="field">
            <span>Problem <span className="muted">(optional — otherwise the interviewer picks one)</span></span>
            <input value={problem} onChange={(e) => setProblem(e.target.value)} placeholder="e.g. a ride-sharing app that matches drivers in real time" autoComplete="off" />
          </label>

          <label className="field">
            <span>Focus <span className="muted">(optional)</span></span>
            <input value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="e.g. push me on failure modes and costs" autoComplete="off" />
          </label>

          <div className="row2">
            <label className="field">
              <span>Interview language</span>
              <select value={lang} onChange={(e) => setLang(e.target.value)}>
                {Object.entries(meta.languages).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Microphone</span>
              <select value={micMode} onChange={(e) => setMicMode(e.target.value as MicMode)}>
                <option value="hands-free">Hands-free (detects when you talk)</option>
                <option value="ptt">Push to talk</option>
              </select>
            </label>
          </div>

          <details className="advanced">
            <summary>Models</summary>
            <div className="row2">
              <label className="field">
                <span>Interviewer</span>
                <select value={model} onChange={(e) => setModel(e.target.value)}>
                  {meta.models.map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Final evaluation</span>
                <select value={feedbackModel} onChange={(e) => setFeedbackModel(e.target.value)}>
                  {meta.models.map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
            </div>
            <p className="hint">They run on your Claude Code login. A fast model works best for the conversation; a deeper one for the evaluation.</p>
          </details>

          <MicCheck />

          <button type="submit" className="btn primary big" disabled={starting}>
            {starting ? "Preparing…" : "▶ Start interview"}
          </button>
        </form>

        <div className="card history">
          <h2>Past sessions</h2>
          <ul className="history-list">
            {history === null ? <li className="empty">Loading…</li>
              : history.length === 0 ? <li className="empty">No sessions yet. Start your first one!</li>
              : history.map((s) => <HistoryItem key={s.id} s={s} />)}
          </ul>
        </div>
      </div>
    </div>
  );
}

function HistoryItem({ s }: { s: SessionSummary }) {
  const d = new Date(s.created * 1000);
  const when = d.toLocaleDateString("en", { day: "numeric", month: "short" }) + " · " +
    d.toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" });
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
      setMsg("This browser doesn't allow the microphone here. Open the app in Chrome or Safari.");
      return;
    }
    stop();
    const m = new Mic({ mode: "ptt", onLevel: setLevel });
    mic.current = m;
    try {
      await m.enable();
      setMsg("Talk: the bar should move.");
      timer.current = window.setTimeout(() => { stop(); setMsg("Done."); }, 8000);
    } catch (e) {
      setMsg("No microphone access: " + (e as Error).message);
    }
  }

  return (
    <div className="mic-check">
      <button type="button" className="btn" onClick={test}>🎙 Test microphone</button>
      <div className="meter"><div style={{ width: `${Math.min(100, level * 900)}%` }} /></div>
      <span className="muted small">{msg}</span>
    </div>
  );
}
