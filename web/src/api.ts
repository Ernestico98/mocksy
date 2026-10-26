// Typed client for the FastAPI backend.

export interface Phase { key: string; label: string; minutes: number }

export interface Pack {
  id: string;
  title: string;
  description: string;
  company: string;
  default_language: string;
  phases: Phase[];
}

export interface Settings {
  model: string;
  feedback_model: string;
  feedback_language: string;
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
  model: string;
  feedback_model: string;
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

async function request<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json() as Promise<T>;
}

const s = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;

export const api = {
  meta: () => request<AppMeta>("/api/meta"),
  saveSettings: (patch: Partial<Settings>) => request<Settings>("/api/settings", patch),
  sessions: () => request<SessionSummary[]>("/api/sessions"),
  createSession: (body: {
    pack: string; company: string; level: string; focus: string; problem: string; lang: string; model: string; feedback_model: string;
  }) => request<SessionData>("/api/sessions", body),
  session: (id: string) => request<SessionData>(s(id)),
  say: (id: string, text: string, kind: "speech" | "typed" = "speech") => request(`${s(id)}/say`, { text, kind }),
  look: (id: string, text: string) => request(`${s(id)}/look`, { text }),
  phase: (id: string, key: string) => request(`${s(id)}/phase`, { key }),
  interrupt: (id: string, message_id: number, heard_index: number) =>
    request(`${s(id)}/interrupt`, { message_id, heard_index }),
  activity: (id: string, speaking: boolean) => request(`${s(id)}/activity`, { speaking }),
  canvas: (id: string, body: { lines: string[]; png: string | null; scene: string }) => request(`${s(id)}/canvas`, body),
  end: (id: string) => request(`${s(id)}/end`, {}),
  stop: (id: string) => request(`${s(id)}/stop`, {}),
  resume: (id: string) => request(`${s(id)}/resume`, {}),
  async transcribe(id: string, wav: Blob): Promise<string> {
    const fd = new FormData();
    fd.append("audio", wav, "turn.wav");
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
