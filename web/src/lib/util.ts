import DOMPurify from "dompurify";
import { marked } from "marked";

export const fmt = (sec: number) => {
  const s = Math.max(0, Math.floor(sec));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

export const markdown = (text?: string) => DOMPurify.sanitize(marked.parse(text || "", { async: false }));

export const store = {
  get(key: string, fallback: string) {
    try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
  },
  set(key: string, value: string) {
    try { localStorage.setItem(key, value); } catch { /* private mode: not persisted */ }
  },
};

export const VERDICT_TONE: Record<string, "good" | "bad"> = {
  "strong hire": "good", hire: "good", "lean hire": "good", "lean no hire": "bad", "no hire": "bad",
};

export const STATUS_LABEL: Record<string, string> = {
  live: "in progress", evaluating: "evaluating…", ended: "not evaluated", setup: "preparing",
};

export function voiceName(v: string) {
  const [p, n = ""] = v.split("_");
  const accent = ({ a: "US", b: "UK", e: "ES" } as Record<string, string>)[p[0]] || "";
  return `${n.charAt(0).toUpperCase() + n.slice(1)} (${accent} ${p[1] === "f" ? "♀" : "♂"})`;
}

export const prefersDark = () => matchMedia("(prefers-color-scheme: dark)").matches;

/** Navigate: home, or a session view. */
export const go = (sessionId?: string) => {
  location.hash = sessionId ? `#/s/${encodeURIComponent(sessionId)}` : "";
};
