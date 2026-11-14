import { useEffect, useRef, useState } from "react";
import {
  api, subscribe, type AppMeta, type Estimate, type ModelAnswerMode, type ModelAnswerState, type ModelAnswerStatus,
  type SessionMeta, type Settings,
} from "../api";
import { fmt, markdown } from "../lib/util";
import { useConfirm } from "./Confirm";

interface Props {
  meta: SessionMeta;
  app: AppMeta;
}

const MODE_TEXT: Record<ModelAnswerMode, { title: string; what: string; button: string }> = {
  video: {
    title: "Explainer video",
    what: "A narrated, animated walkthrough (~5–6 min) with chapters, ready to upload, plus the diagram and the script.",
    button: "Generate video",
  },
  diagram: {
    title: "Diagram + write-up only",
    what: "The final solution as a diagram (SVG + PNG) and the written explanation. One model call, no video.",
    button: "Generate diagram",
  },
};

const usd = (n: number) => `$${n.toFixed(2)}`;
const range = (e: Estimate) => `${usd(e.usd[0])}–${usd(e.usd[1])}`;
const tokens = (e: Estimate) => {
  const total = Object.values(e.tokens).reduce((a, b) => a + b, 0);
  return total >= 1e6 ? `${(total / 1e6).toFixed(1)}M` : `${Math.round(total / 1000)}k`;
};
const STATUS_ICON: Record<string, string> = { done: "✓", running: "●", failed: "✕", cancelled: "■", pending: "○", skipped: "–" };

export default function ModelAnswerPanel({ meta, app }: Props) {
  const ask = useConfirm();
  const [data, setData] = useState<ModelAnswerStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const id = meta.id;

  const load = () => api.modelAnswer(id).then(setData).catch((e) => setError(String(e.message || e)));
  useEffect(() => {
    load();
    return subscribe((ev) => {
      if (ev.type === "model_answer" && ev.session === id) setData((d) => (d ? { ...d, state: ev.state } : d));
      if (ev.type === "settings") load();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (!data) return <div className="evaluating">{error || "Loading…"}</div>;
  if (data.allowed === false && !data.state) return <div className="evaluating">{data.blocked}</div>;
  const st = data.state;

  async function generate(mode: ModelAnswerMode, fresh = false) {
    const e = data!.estimates[mode];
    const text = `${MODE_TEXT[mode].title}: estimated ${range(e)}, typically ${usd(e.typical_usd)} (API-equivalent, as Claude Code `
      + `reports it; about ${tokens(e)} tokens, mostly cache reads) and ${e.minutes[0]}–${e.minutes[1]} min. `
      + `It stops cleanly at the ${usd(e.budget_usd)} budget cap.`;
    if (!(await ask(text, "Generate"))) return;
    await run(() => api.startModelAnswer(id, mode, fresh).then(setData));
  }

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try { await fn(); } catch (e) { setError(String((e as Error).message || e)); } finally { setBusy(false); }
  }

  async function cancel() {
    await run(() => api.cancelModelAnswer(id));
  }

  async function startOver() {
    if (!(await ask("Delete the generated model answer? Its files (video, script, diagram) are removed for good.", "Delete"))) return;
    await run(async () => { await api.deleteModelAnswer(id); await load(); });
  }

  return (
    <div className="ma">
      <p className="small muted ma-intro">
        A neutral explainer of how to solve this problem well, written to be shared or published: it never mentions this
        interview, mistakes or scores{meta.company && !data.settings.ma_name_company ? `, nor ${meta.company}` : ""}. The
        evaluation only decides which concepts get more time.
      </p>
      {error && <div className="banner">{error}</div>}

      {!st && <Start data={data} busy={busy} onGenerate={generate} />}
      {st && <Progress st={st} />}
      {st?.status === "running" && <button className="btn" onClick={cancel} disabled={busy}>Cancel</button>}
      {st && (st.status === "failed" || st.status === "cancelled") && (
        <div className="ma-actions">
          <button className="btn primary" disabled={busy} onClick={() => run(() => api.startModelAnswer(id, st.mode).then(setData))}>
            Retry from {st.labels[Object.keys(st.stages).find((k) => st.stages[k].status !== "done") || ""] || "the start"}
          </button>
          <button className="btn ghost" disabled={busy} onClick={startOver}>Start over</button>
        </div>
      )}
      {st?.status === "done" && <Result st={st} />}
      {st?.status === "done" && (
        <div className="ma-actions">
          {st.mode === "diagram" && (
            <button className="btn primary" disabled={busy || !!data.ready} title={data.ready || ""} onClick={() => generate("video")}>
              Make the video too (reuses this script)
            </button>
          )}
          <button className="btn ghost" disabled={busy} onClick={startOver}>Delete and start over</button>
        </div>
      )}
      {st?.status !== "running" && <SettingsBox app={app} meta={meta} settings={data.settings} onSaved={load} />}
    </div>
  );
}

function Start({ data, busy, onGenerate }: { data: ModelAnswerStatus; busy: boolean; onGenerate: (m: ModelAnswerMode) => void }) {
  return (
    <div className="ma-modes">
      {(["video", "diagram"] as ModelAnswerMode[]).map((m) => {
        const e = data.estimates[m];
        const blocked = m === "video" ? data.ready : null;
        return (
          <div className="ma-mode" key={m}>
            <strong>{MODE_TEXT[m].title}</strong>
            <span className="small muted">{MODE_TEXT[m].what}</span>
            <span className="small">≈ {range(e)} (typically {usd(e.typical_usd)}) · {e.minutes[0]}–{e.minutes[1]} min · cap {usd(e.budget_usd)}</span>
            {blocked && <span className="small warn">{blocked}</span>}
            <button className={`btn${m === "video" ? " primary" : ""}`} disabled={busy || !!blocked} onClick={() => onGenerate(m)}>
              {MODE_TEXT[m].button}
            </button>
          </div>
        );
      })}
    </div>
  );
}

function Progress({ st }: { st: ModelAnswerState }) {
  const keys = Object.keys(st.labels);
  const elapsed = (st.ended_at || Date.now() / 1000) - st.started_at;
  return (
    <div className="ma-progress">
      <ol className="ma-stages">
        {keys.map((k) => {
          const s = st.stages[k] || { status: "pending" };
          return (
            <li key={k} className={s.status}>
              <span className="ic" aria-hidden>{STATUS_ICON[s.status] || "○"}</span>
              <span className="lab">{st.labels[k]}</span>
              <span className="det small muted">{s.detail}</span>
              {s.status === "running" && s.progress != null && (
                <div className="ma-bar"><div style={{ width: `${Math.round(s.progress * 100)}%` }} /></div>
              )}
            </li>
          );
        })}
      </ol>
      <div className="small muted">
        Spent {usd(st.cost_usd)} of the {usd(st.budget_usd)} cap · {st.runs.length} model run{st.runs.length === 1 ? "" : "s"}
        {st.status === "running" ? ` · ${fmt(elapsed)} elapsed` : ""}
      </div>
      {st.error && <div className="banner ma-error">{st.error}</div>}
    </div>
  );
}

function Result({ st }: { st: ModelAnswerState }) {
  const video = useRef<HTMLVideoElement>(null);
  const [script, setScript] = useState("");
  const o = st.outputs;
  useEffect(() => {
    if (!o.script) return;
    fetch(o.script.url).then((r) => r.text()).then((t) => setScript(t.replace(/^!\[[^\]]*\]\([^)]*\)\n?/m, "")));
  }, [o.script?.url]);
  const diagram = o.diagram_png || o.diagram_svg;

  return (
    <div className="ma-result">
      {o.video && (
        <>
          <video ref={video} className="ma-video" src={o.video.url} controls preload="metadata" />
          {st.chapters && (
            <ol className="ma-chapters">
              {st.chapters.map((c) => (
                <li key={c.t}>
                  <button onClick={() => { if (video.current) { video.current.currentTime = c.t; video.current.play(); } }}>
                    <span className="t">{fmt(c.t)}</span> {c.title}
                  </button>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
      <div className="ma-downloads">
        {(["video", "youtube", "script", "diagram_png", "diagram_svg"] as const).map((k) => o[k] && (
          <a key={k} className="btn" href={o[k]!.url} download={o[k]!.name}>
            {{ video: "MP4", youtube: "YouTube text", script: "Script (.md)", diagram_png: "Diagram PNG", diagram_svg: "Diagram SVG" }[k]}
          </a>
        ))}
      </div>
      {st.review?.length ? (
        <div className="banner ma-review">
          <strong>Review before publishing</strong>
          <ul className="small">{st.review.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </div>
      ) : null}
      {st.qa && (
        <p className="small muted">
          Review: {st.qa.summary}{st.qa.fixed ? ` ${st.qa.fixed} sent back for fixes.` : ""}
        </p>
      )}
      {st.script?.assumptions?.length ? (
        <>
          <h3>Assumed figures</h3>
          <ul className="small">{st.script.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </>
      ) : null}
      {diagram && (
        <>
          <h3>Solution diagram</h3>
          <a href={diagram.url} target="_blank" rel="noreferrer"><img className="ma-diagram" src={diagram.url} alt="Solution diagram" /></a>
        </>
      )}
      {script && <div className="report" dangerouslySetInnerHTML={{ __html: markdown(script) }} />}
    </div>
  );
}

function SettingsBox({ app, meta, settings, onSaved }: { app: AppMeta; meta: SessionMeta; settings: Partial<Settings>; onSaved: () => void }) {
  const [err, setErr] = useState("");
  const save = (patch: Partial<Settings>) => api.saveSettings(patch).then(onSaved).catch((e) => setErr(String(e.message || e)));
  const model = (key: "ma_script_model" | "ma_scene_model" | "ma_qa_model", label: string) => (
    <label className="field">
      {label}
      <select value={settings[key] || ""} onChange={(e) => save({ [key]: e.target.value })}>
        {[...new Set([...app.models, settings[key] || ""])].filter(Boolean).map((m) => <option key={m}>{m}</option>)}
      </select>
    </label>
  );
  return (
    <details className="advanced ma-settings">
      <summary>Model answer settings</summary>
      <div className="row2">
        {model("ma_script_model", "Script (judgment)")}
        {model("ma_scene_model", "Scenes and fixes")}
        {model("ma_qa_model", "Review (QA)")}
        <label className="field">
          Budget cap (USD)
          <input type="number" min={0.5} step={0.5} defaultValue={settings.ma_budget_usd}
            onBlur={(e) => +e.target.value > 0 && save({ ma_budget_usd: +e.target.value })} />
        </label>
        <label className="field">
          Scenes
          <input type="number" min={2} max={10} defaultValue={settings.ma_scenes}
            onBlur={(e) => +e.target.value >= 2 && save({ ma_scenes: Math.min(10, Math.round(+e.target.value)) })} />
        </label>
      </div>
      <label className="check small">
        <input type="checkbox" checked={!!settings.ma_fix_rounds} onChange={(e) => save({ ma_fix_rounds: e.target.checked ? 1 : 0 })} />
        One round of fixes after the review
      </label>
      <label className="check small">
        <input type="checkbox" checked={!!settings.ma_name_company} onChange={(e) => save({ ma_name_company: e.target.checked })} />
        Allow naming the target company{meta.company ? ` (${meta.company})` : ""} in the video
      </label>
      <p className="hint">Each video is bounded: one script run, two scene workers (at most 2 render rounds per scene),
        one review pass, one fix round. The cap stops the generation if it's reached.</p>
      {err && <p className="hint warn">{err}</p>}
    </details>
  );
}
