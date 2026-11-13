// Pack editor logic: form state <-> API shape, and the same validation rules as app/packs.py.
import type { PackData } from "../api";

export interface PhaseForm { key: string; label: string; minutes: string }

/** What the editor form holds: numbers as typed text, the rubric one dimension per line. */
export interface PackForm {
  id: string;
  title: string;
  description: string;
  company: string;
  default_language: string;
  whiteboard: boolean;
  phases: PhaseForm[];
  rubric: string;
  body: string;
  extra: Record<string, unknown>;
}

/** Field path ("title", "phases.1.key"…) → message; the backend uses the same paths. */
export type Errors = Record<string, string>;

export const ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
export const PHASE_KEY_RE = /^[a-z][a-z0-9_]{0,31}$/;
export const MAX_MINUTES = 180;

export const emptyForm = (): PackForm => ({
  id: "", title: "", description: "", company: "", default_language: "en", whiteboard: true,
  phases: [{ key: "", label: "", minutes: "15" }], rubric: "", body: "", extra: {},
});

export const toForm = (p: PackData): PackForm => ({
  id: p.id, title: p.title, description: p.description, company: p.company,
  default_language: p.default_language || "en", whiteboard: p.whiteboard,
  phases: p.phases.map((ph) => ({ key: ph.key, label: ph.label, minutes: String(ph.minutes) })),
  rubric: p.rubric.join("\n"), body: p.body, extra: p.extra || {},
});

const rubricLines = (text: string) => text.split("\n").map((r) => r.trim()).filter(Boolean);

export const fromForm = (f: PackForm): Omit<PackData, "builtin"> => ({
  id: f.id.trim(), title: f.title.trim(), description: f.description.trim(), company: f.company.trim(),
  default_language: f.default_language, whiteboard: f.whiteboard,
  phases: f.phases.map((ph) => ({ key: ph.key.trim(), label: ph.label.trim(), minutes: Number(ph.minutes.trim()) || 0 })),
  rubric: rubricLines(f.rubric), body: f.body.trim(), extra: f.extra,
});

/** "Product Sense: Round 2!" → "product-sense-round-2" (valid as a pack id / filename). */
export function slugify(text: string, max = 64): string {
  const s = text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+/, "").slice(0, max).replace(/-+$/, "");
  return s;
}

/** Phase key from its label: "High-level design" → "high_level_design". */
export const phaseKey = (label: string) => slugify(label, 32).replace(/-/g, "_").replace(/^[0-9_]+/, "");

/** `base`, or `base-2`, `base-3`… whichever isn't taken. */
export function uniqueId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const root = slugify(base) || "interview";
  if (!used.has(root)) return root;
  for (let n = 2; ; n++) {
    const id = `${root.slice(0, 60)}-${n}`;
    if (!used.has(id)) return id;
  }
}

/** A personal copy of any pack (built-ins included). */
export function duplicate(p: PackData, taken: Iterable<string>): PackForm {
  return { ...toForm(p), id: uniqueId(`${p.id}-copy`, taken), title: `${p.title} (copy)` };
}

/** `taken`: ids of the other packs (excluding the one being edited). */
export function validate(f: PackForm, taken: Iterable<string>): Errors {
  const e: Errors = {};
  const id = f.id.trim();
  if (!ID_RE.test(id)) e.id = "Use 1-64 lowercase letters, digits or dashes, starting with a letter or digit.";
  else if (new Set(taken).has(id)) e.id = `An interview type called "${id}" already exists.`;
  if (!f.title.trim()) e.title = "A title is required.";
  if (f.phases.length === 0) e.phases = "Add at least one phase.";
  const seen = new Set<string>();
  f.phases.forEach((ph, i) => {
    const key = ph.key.trim();
    if (!PHASE_KEY_RE.test(key)) e[`phases.${i}.key`] = "Lowercase letters, digits and underscores, starting with a letter.";
    else if (seen.has(key)) e[`phases.${i}.key`] = `Duplicate key "${key}".`;
    seen.add(key);
    if (!ph.label.trim()) e[`phases.${i}.label`] = "A label is required.";
    const m = ph.minutes.trim();
    if (!/^\d+$/.test(m) || +m < 1 || +m > MAX_MINUTES) e[`phases.${i}.minutes`] = `1 to ${MAX_MINUTES} min.`;
  });
  const rubric = rubricLines(f.rubric);
  if (rubric.length === 0) e.rubric = "Add at least one rubric dimension.";
  else if (new Set(rubric).size !== rubric.length) e.rubric = "Rubric dimensions must be unique.";
  if (!f.body.trim()) e.body = "The interviewer brief is required.";
  return e;
}

export const totalMinutes = (phases: { minutes: number | string }[]) =>
  phases.reduce((t, p) => t + (Number(p.minutes) || 0), 0);
