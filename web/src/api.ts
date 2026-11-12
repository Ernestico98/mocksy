// Typed client for the FastAPI backend.

export interface Phase { key: string; label: string; minutes: number }

export interface Pack {
  id: string;
  title: string;
  description: string;
  company: string;
  default_language: string;
  phases: Phase[];
  whiteboard: boolean;
  builtin: boolean;
}

/** A full interview pack, as the editor reads and writes it. */
export interface PackData extends Pack {
  rubric: string[];
  body: string;
  /** header fields the editor doesn't know about, kept as they are */
  extra: Record<string, unknown>;
}

export interface Settings {
  model: string;
  feedback_model: string;
  feedback_language: string;
  /** interviewer reasoning effort: "default" (the CLI's choice), "low", "medium", "high" */
  effort: string;
  speed: number;
  voice_en: string;
  voice_es: string;
}

export interface AppMeta {
  packs: Pack[];
  languages: Record<string, string>;
  voices: string[];
  settings: Settings;
  models: string[];
  efforts: string[];
}

export type SessionStatus = "setup" | "live" | "evaluating" | "ended" | "done";

export interface SessionMeta {
  id: string;
  created: number;
  status: SessionStatus;
  pack: string;
  pack_title: string;
  company: string;
  level: string;
  focus: string;
  problem?: string;
  lang: string;
  phases: Phase[];
  rubric: string[];
  whiteboard?: boolean;
  model: string;
  feedback_model: string;
  effort?: string;
  started_at?: number;
  ended_at?: number;
  phase?: { key: string; started_at: number };
  verdict?: string;
  cost_usd?: number;
}

export interface Message {
  id: number;
  role: "interviewer" | "candidate" | "event";
  kind: "speech" | "typed" | "note" | "look" | "phase";
  text: string;
  ts: number;
  done?: boolean;
  interrupted?: boolean;
  heard?: string;
  audio?: Record<string, string>;
  by?: string;
  /** candidate turns: how long the interviewer took to answer, per stage (see app/latency.py) */
  latency?: Latency;
}

export interface Latency {
  kind: string;
  silence_ms?: number;
  upload_ms?: number;
  stt_ms?: number;
  handoff_ms?: number;
  model_ms?: number;
  chunk_ms?: number;
  tts_ms?: number;
  play_ms?: number;
  total_ms?: number;
  total_to?: "played" | "tts_ready";
  tools_before_speech?: number;
}

export interface Feedback {
  verdict?: string;
  summary?: string;
  scores?: Record<string, number | null>;
  top3?: string[];
  review?: { id: number; topic: string; what: string }[];
  report?: string;
}

export interface SessionData {
  meta: SessionMeta;
  messages: Message[];
  feedback: Feedback | null;
}

export interface SessionSummary {
  id: string;
  created: number;
  pack_title: string;
  company: string;
  status: SessionStatus;
  verdict?: string;
  lang: string;
  minutes: number;
}

export type ServerEvent =
  | { type: "settings"; settings: Settings }
  | { type: "message"; session: string; message: Message }
  | { type: "audio"; session: string; message_id: number; index: number; url: string }
  | { type: "status"; session: string; state: string; detail?: string }
  | { type: "meta"; session: string; meta: SessionMeta }
  | { type: "feedback"; session: string; feedback: Feedback | null };

/** A failed request; `errors` holds per-field validation messages (422 from the pack API). */
export class ApiError extends Error {
  constructor(message: string, public status: number, public errors: Record<string, string> = {}) {
    super(message);
  }
}

async function request<T>(path: string, body?: unknown, method = body === undefined ? "GET" : "POST"): Promise<T> {
  const r = await fetch(path, body === undefined ? { method } : {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const text = await r.text();
    let data: { detail?: unknown; errors?: Record<string, string> } = {};
    try { data = JSON.parse(text); } catch { /* not JSON */ }
    const msg = typeof data.detail === "string" ? data.detail : text || r.statusText;
    throw new ApiError(msg, r.status, data.errors);
  }
  return r.json() as Promise<T>;
}

const p = (id: string) => `/api/packs/${encodeURIComponent(id)}`;

const s = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;

export const api = {
  meta: () => request<AppMeta>("/api/meta"),
  saveSettings: (patch: Partial<Settings>) => request<Settings>("/api/settings", patch),
  sessions: () => request<SessionSummary[]>("/api/sessions"),
  createSession: (body: {
    pack: string; company: string; level: string; focus: string; problem: string; lang: string; model: string; feedback_model: string; effort: string;
  }) => request<SessionData>("/api/sessions", body),
  session: (id: string) => request<SessionData>(s(id)),
  packs: () => request<PackData[]>("/api/packs"),
  pack: (id: string) => request<PackData>(p(id)),
  createPack: (pack: Omit<PackData, "builtin">) => request<PackData>("/api/packs", pack),
  updatePack: (id: string, pack: Omit<PackData, "builtin">) => request<PackData>(p(id), pack, "PUT"),
  deletePack: (id: string) => request(p(id), undefined, "DELETE"),
  packMarkdown: (pack: Omit<PackData, "builtin">) => request<{ raw: string }>("/api/pack-markdown", pack),
  parsePackMarkdown: (raw: string) => request<PackData>("/api/pack-markdown/parse", { raw }),
  say: (id: string, text: string, kind: "speech" | "typed" = "speech") => request(`${s(id)}/say`, { text, kind }),
  look: (id: string, text: string) => request(`${s(id)}/look`, { text }),
  phase: (id: string, key: string) => request(`${s(id)}/phase`, { key }),
  interrupt: (id: string, message_id: number, heard_index: number) =>
    request(`${s(id)}/interrupt`, { message_id, heard_index }),
  activity: (id: string, speaking: boolean) => request(`${s(id)}/activity`, { speaking }),
  /** `wait_ms`: from the clip's "audio" event to playback starting (browser clock) */
  played: (id: string, message_id: number, index: number, wait_ms: number) =>
    request(`${s(id)}/played`, { message_id, index, wait_ms }),
  canvas: (id: string, body: { lines: string[]; png: string | null; scene: string }) => request(`${s(id)}/canvas`, body),
  end: (id: string) => request(`${s(id)}/end`, {}),
  stop: (id: string) => request(`${s(id)}/stop`, {}),
  resume: (id: string) => request(`${s(id)}/resume`, {}),
  /** `timing`: when the VAD ended the turn (performance.now()) and the pause it waited, for latency stats. */
  async transcribe(id: string, wav: Blob, timing?: { vadAt: number; silenceMs: number }): Promise<string> {
    const fd = new FormData();
    fd.append("audio", wav, "turn.wav");
    if (timing) {
      fd.append("vad_age_ms", String(Math.round(performance.now() - timing.vadAt)));
      fd.append("silence_ms", String(Math.round(timing.silenceMs)));
    }
    const r = await fetch(`${s(id)}/transcribe`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    return ((await r.json()) as { text: string }).text;
  },
  async scene(id: string): Promise<unknown | null> {
    const r = await fetch(`/files/${encodeURIComponent(id)}/canvas.excalidraw`);
    return r.ok ? r.json() : null;
  },
};

// One EventSource for the whole app; components subscribe to it.
type Listener = (ev: ServerEvent) => void;
const listeners = new Set<Listener>();
let source: EventSource | null = null;

export function subscribe(fn: Listener): () => void {
  if (!source) {
    source = new EventSource("/api/events");
    source.onmessage = (e) => {
      const ev = JSON.parse(e.data) as ServerEvent;
      listeners.forEach((l) => l(ev));
    };
  }
  listeners.add(fn);
  return () => listeners.delete(fn);
}
