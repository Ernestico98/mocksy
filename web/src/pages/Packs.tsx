import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, api, type AppMeta, type PackData } from "../api";
import { useConfirm } from "../components/Confirm";
import {
  duplicate, emptyForm, fromForm, phaseKey, slugify, toForm, totalMinutes, validate,
  type Errors, type PackForm, type PhaseForm,
} from "../lib/packs";

export type PackRoute = { mode: "list" } | { mode: "new"; from?: string } | { mode: "edit"; id: string };

const goPacks = (path = "") => { location.hash = `#/packs${path}`; };

/** Interview types: list, create, edit, duplicate, delete (personal packs only; built-ins are read-only). */
export default function Packs({ route, meta, onChanged }: { route: PackRoute; meta: AppMeta; onChanged: () => void }) {
  const [packs, setPacks] = useState<PackData[] | null>(null);
  const [error, setError] = useState("");
  const load = () => api.packs().then(setPacks).catch((e: Error) => setError(e.message));

  useEffect(() => { document.title = "Interview types · Mocksy"; void load(); }, [route]);

  const changed = () => { onChanged(); void load(); };

  return (
    <div className="home packs-page">
      <header className="home-head">
        <a className="back-link" href="#/">← Home</a>
        <h1 className="brand">Interview types</h1>
        <p className="muted">Each type is a pack: phases with a time budget, a rubric and the interviewer’s brief.
          Built-in types are read-only; duplicate one to make it your own. Yours are saved in <code>data/packs/</code>.</p>
      </header>
      {error ? <div className="banner">{error}</div>
        : !packs ? <div className="empty">Loading…</div>
        : route.mode === "list" ? <PackList packs={packs} onChanged={changed} />
        : <PackEditor key={JSON.stringify(route)} route={route} packs={packs} meta={meta} onChanged={changed} />}
    </div>
  );
}

// ------------------------------------------------------------------ list

function PackList({ packs, onChanged }: { packs: PackData[]; onChanged: () => void }) {
  const ask = useConfirm();

  async function remove(p: PackData) {
    if (!(await ask(`Delete “${p.title}”? Past sessions keep working.`, "Delete"))) return;
    try { await api.deletePack(p.id); onChanged(); }
    catch (e) { alert("Couldn't delete it: " + (e as Error).message); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>All interview types</h2>
        <button className="btn primary" onClick={() => goPacks("/new")}>+ New interview type</button>
      </div>
      <ul className="pack-list">
        {packs.map((p) => (
          <li key={p.id}>
            <div className="pack-info">
              <strong>{p.title}</strong>
              <span className="badges">
                <span className="badge">{p.builtin ? "Built-in" : "Personal"}</span>
                {!p.whiteboard && <span className="badge">No whiteboard</span>}
              </span>
              {p.description && <span className="muted small">{p.description}</span>}
              <span className="muted small">
                {p.phases.map((ph) => `${ph.label} ${ph.minutes}′`).join(" · ")} · {totalMinutes(p.phases)} min
              </span>
            </div>
            <div className="pack-actions">
              <button className="btn" onClick={() => goPacks(`/edit/${encodeURIComponent(p.id)}`)}>{p.builtin ? "View" : "Edit"}</button>
              <button className="btn" onClick={() => goPacks(`/new/${encodeURIComponent(p.id)}`)}>Duplicate</button>
              {!p.builtin && <button className="btn danger" onClick={() => void remove(p)}>Delete</button>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ editor

function PackEditor({ route, packs, meta, onChanged }: {
  route: Exclude<PackRoute, { mode: "list" }>; packs: PackData[]; meta: AppMeta; onChanged: () => void;
}) {
  const ask = useConfirm();
  const original = route.mode === "edit" ? packs.find((p) => p.id === route.id) : undefined;
  const source = route.mode === "new" && route.from ? packs.find((p) => p.id === route.from) : undefined;
  const readOnly = !!original?.builtin;

  const initial = useMemo<PackForm>(() => {
    if (original) return toForm(original);
    if (source) return duplicate(source, packs.map((p) => p.id));
    return emptyForm();
  }, [original, source, packs]);

  const [form, setForm] = useState(initial);
  const [idTouched, setIdTouched] = useState(route.mode === "edit" || !!source);
  const [tried, setTried] = useState(false);
  const [serverErrors, setServerErrors] = useState<Errors>({});
  const [raw, setRaw] = useState<string | null>(null);  // non-null: raw markdown mode
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const taken = packs.map((p) => p.id).filter((id) => id !== original?.id);
  const errors: Errors = { ...(tried ? validate(form, taken) : {}), ...serverErrors };
  const dirty = raw !== null || JSON.stringify(form) !== JSON.stringify(initial);

  if (route.mode === "edit" && !original) return <div className="card">Interview type not found. <a href="#/packs">Back</a></div>;

  const set = (patch: Partial<PackForm>) => {
    setServerErrors({});
    setForm((f) => {
      const next = { ...f, ...patch };
      if (!idTouched && "title" in patch) next.id = slugify(next.title);
      return next;
    });
  };
  const setPhase = (i: number, patch: Partial<PhaseForm>) =>
    set({ phases: form.phases.map((ph, j) => (j === i ? { ...ph, ...patch } : ph)) });
  const movePhase = (i: number, d: number) => {
    const phases = form.phases.slice();
    [phases[i], phases[i + d]] = [phases[i + d], phases[i]];
    set({ phases });
  };

  async function toggleRaw(on: boolean) {
    setMessage("");
    try {
      if (on) {
        setRaw((await api.packMarkdown(fromForm(form))).raw);
      } else if (raw !== null) {
        setForm({ ...toForm(await api.parsePackMarkdown(raw)), id: form.id });
        setRaw(null);
      }
    } catch (e) {
      setMessage(errorText(e));
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (readOnly) return;
    setMessage("");
    setTried(true);
    let f = form;
    if (raw !== null) {
      try { f = { ...toForm(await api.parsePackMarkdown(raw)), id: form.id }; }
      catch (err) { setMessage(errorText(err)); return; }
      setForm(f);
      setRaw(null);
    }
    if (Object.keys(validate(f, taken)).length) { setMessage("Please fix the highlighted fields."); return; }
    setSaving(true);
    try {
      if (original) await api.updatePack(original.id, fromForm(f));
      else await api.createPack(fromForm(f));
      onChanged();
      goPacks();
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.errors).length) {
        setServerErrors(err.errors);
        setMessage("Please fix the highlighted fields.");
      } else setMessage(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  async function cancel() {
    if (dirty && !readOnly && !(await ask("Discard your changes?", "Discard"))) return;
    goPacks();
  }

  async function remove() {
    if (!original || !(await ask(`Delete “${original.title}”? Past sessions keep working.`, "Delete"))) return;
    try { await api.deletePack(original.id); onChanged(); goPacks(); }
    catch (err) { setMessage(errorText(err)); }
  }

  const heading = readOnly ? original!.title : original ? `Edit “${original.title}”` : source ? `Duplicate “${source.title}”` : "New interview type";

  return (
    <form className="card pack-editor" onSubmit={save} noValidate>
      <div className="card-head">
        <h2>{heading}</h2>
        {!readOnly && (
          <label className="toggle small">
            <input type="checkbox" checked={raw !== null} onChange={(e) => void toggleRaw(e.target.checked)} />
            Edit raw markdown
          </label>
        )}
      </div>

      {readOnly && (
        <div className="banner">
          Built-in interview types are read-only.{" "}
          <button type="button" className="btn" onClick={() => goPacks(`/new/${encodeURIComponent(original!.id)}`)}>Duplicate as a personal type</button>
        </div>
      )}
      {errors.raw && <div className="banner error-banner">{errors.raw}</div>}

      {raw !== null ? (
        <Field label="Markdown" hint={<>TOML header between <code>+++</code> lines, then the interviewer brief. Same format as the files in <code>prompts/packs/</code>.</>}>
          <textarea className="mono" rows={28} value={raw} onChange={(e) => setRaw(e.target.value)} spellCheck={false} />
        </Field>
      ) : (
        <fieldset disabled={readOnly} className="plain">
          <div className="row2">
            <Field label="Title" error={errors.title}>
              <input value={form.title} onChange={(e) => set({ title: e.target.value })} placeholder="e.g. Behavioural (hiring manager)" autoComplete="off" />
            </Field>
            <Field label="File name" error={errors.id} hint={!readOnly && <>Saved as <code>data/packs/{form.id || "…"}.md</code></>}>
              <input value={form.id} onChange={(e) => { setIdTouched(true); set({ id: e.target.value }); }} placeholder="behavioural-hm" autoComplete="off" spellCheck={false} />
            </Field>
          </div>

          <Field label={<>Description <span className="muted">(optional)</span></>}>
            <input value={form.description} onChange={(e) => set({ description: e.target.value })} placeholder="One line shown on the home screen" autoComplete="off" />
          </Field>

          <div className="row2">
            <Field label={<>Default company <span className="muted">(optional)</span></>} hint="Pre-fills the company when you pick this type.">
              <input value={form.company} onChange={(e) => set({ company: e.target.value })} autoComplete="off" />
            </Field>
            <Field label="Default language" error={errors.default_language}>
              <select value={form.default_language} onChange={(e) => set({ default_language: e.target.value })}>
                {Object.entries(meta.languages).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
          </div>

          <label className="toggle field">
            <input type="checkbox" checked={form.whiteboard} onChange={(e) => set({ whiteboard: e.target.checked })} />
            <span>Whiteboard <span className="muted">— show the drawing board. Turn it off for conversation-only rounds (behavioural, culture…).</span></span>
          </label>

          <fieldset className="field plain">
            <legend>Phases <span className="muted">· {totalMinutes(form.phases)} min in total</span></legend>
            {errors.phases && <span className="error">{errors.phases}</span>}
            <div className="phase-rows">
              <div className="phase-row head muted small"><span>Label</span><span>Key</span><span>Minutes</span><span /></div>
              {form.phases.map((ph, i) => (
                <div key={i} className="phase-row">
                  <Cell error={errors[`phases.${i}.label`]}>
                    <input aria-label={`Phase ${i + 1} label`} value={ph.label} placeholder="Requirements"
                      onChange={(e) => setPhase(i, { label: e.target.value })}
                      onBlur={() => { if (!ph.key.trim() && ph.label.trim()) setPhase(i, { key: phaseKey(ph.label) }); }} />
                  </Cell>
                  <Cell error={errors[`phases.${i}.key`]}>
                    <input aria-label={`Phase ${i + 1} key`} value={ph.key} placeholder="requirements" spellCheck={false}
                      onChange={(e) => setPhase(i, { key: e.target.value })} />
                  </Cell>
                  <Cell error={errors[`phases.${i}.minutes`]}>
                    <input aria-label={`Phase ${i + 1} minutes`} value={ph.minutes} inputMode="numeric"
                      onChange={(e) => setPhase(i, { minutes: e.target.value })} />
                  </Cell>
                  {!readOnly && (
                    <span className="phase-tools">
                      <button type="button" className="icon-btn" title="Move up" disabled={i === 0} onClick={() => movePhase(i, -1)}>↑</button>
                      <button type="button" className="icon-btn" title="Move down" disabled={i === form.phases.length - 1} onClick={() => movePhase(i, 1)}>↓</button>
                      <button type="button" className="icon-btn" title="Remove phase" onClick={() => set({ phases: form.phases.filter((_, j) => j !== i) })}>✕</button>
                    </span>
                  )}
                </div>
              ))}
            </div>
            {!readOnly && (
              <button type="button" className="btn ghost" onClick={() => set({ phases: [...form.phases, { key: "", label: "", minutes: "10" }] })}>+ Add phase</button>
            )}
            <p className="hint">The key is what the interviewer uses to move between phases; the label is what you see.</p>
          </fieldset>

          <Field label="Rubric" error={errors.rubric} hint="One dimension per line. The evaluation scores each one from 1 to 5.">
            <textarea rows={6} value={form.rubric} onChange={(e) => set({ rubric: e.target.value })} placeholder={"Problem framing\nCommunication"} />
          </Field>

          <Field label="Interviewer brief" error={errors.body}
            hint="Who the interviewer is, how each phase should go, what to probe and what strong and weak answers look like.">
            <textarea className="mono" rows={18} value={form.body} onChange={(e) => set({ body: e.target.value })}
              placeholder="You are a hiring manager running a behavioural interview…" />
          </Field>
        </fieldset>
      )}

      {message && <p className="error" role="alert">{message}</p>}
      <div className="editor-actions">
        {!readOnly && <button type="submit" className="btn primary" disabled={saving}>{saving ? "Saving…" : "Save"}</button>}
        <button type="button" className="btn" onClick={() => void cancel()}>{readOnly ? "Back" : "Cancel"}</button>
        <span className="spacer" />
        {original && !readOnly && <button type="button" className="btn danger" onClick={() => void remove()}>Delete</button>}
      </div>
    </form>
  );
}

function Field({ label, error, hint, children }: { label: ReactNode; error?: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className={`field${error ? " invalid" : ""}`}>
      <span>{label}</span>
      {children}
      {error ? <span className="error">{error}</span> : hint ? <span className="hint">{hint}</span> : null}
    </label>
  );
}

function Cell({ error, children }: { error?: string; children: ReactNode }) {
  return (
    <span className={`cell${error ? " invalid" : ""}`}>
      {children}
      {error && <span className="error">{error}</span>}
    </span>
  );
}

function errorText(e: unknown): string {
  if (e instanceof ApiError && Object.keys(e.errors).length) return Object.values(e.errors).join(" ");
  return (e as Error).message;
}
