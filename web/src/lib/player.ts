// Plays the interviewer's audio clips in order and tracks what's playing,
// so the mic can detect barge-in and tell the server what was heard.

export interface Clip { message_id: number; index: number; url: string }

type Listener = () => void;

// Fetch and decode a clip as soon as it's queued, so the next one starts without a gap.
function load(clip: Clip) {
  const audio = new Audio();
  audio.preload = "auto";
  audio.src = clip.url;
  return { clip, audio };
}

class Player {
  private queue: { clip: Clip; audio: HTMLAudioElement }[] = [];
  private audio: HTMLAudioElement | null = null;
  private blocked: Clip | null = null;
  current: Clip | null = null;
  needsGesture = false;
  /** Called when a clip actually starts playing (latency stats). */
  onStart: (clip: Clip) => void = () => {};
  private listeners = new Set<Listener>();

  constructor() {
    document.addEventListener("click", () => this.resume());
  }

  get playing() { return !!this.current; }

  subscribe(fn: Listener) {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  }

  private emit() { this.listeners.forEach((l) => l()); }

  enqueue(clip: Clip) {
    this.queue.push(load(clip));
    if (!this.current) this.next();
  }

  replay(audio: Record<string, string>, message_id: number) {
    this.stop();
    Object.entries(audio)
      .sort((a, b) => +a[0] - +b[0])
      .forEach(([index, url]) => this.queue.push(load({ message_id, index: +index, url })));
    this.next();
  }

  stop() {
    this.queue.forEach((q) => q.audio.removeAttribute("src"));
    this.queue = [];
    this.blocked = null;
    const a = this.audio;
    this.audio = null;
    this.current = null;
    a?.pause();
    this.emit();
  }

  private resume() {
    if (this.blocked && !this.current) {
      this.queue.unshift(load(this.blocked));
      this.blocked = null;
      this.needsGesture = false;
      this.next();
    }
  }

  private next() {
    const item = this.queue.shift();
    if (!item) { this.current = null; this.audio = null; this.emit(); return; }
    const { clip, audio } = item;
    this.audio = audio;
    this.current = clip;
    this.emit();
    let done = false;
    const go = () => {
      if (done || this.audio !== audio) return;
      done = true;
      this.next();
    };
    audio.onplaying = () => { if (this.audio === audio) this.onStart(clip); };
    audio.onended = go;
    audio.onerror = go;           // missing/broken clip: skip, never retry
    audio.play().catch((e: DOMException) => {
      if (e.name !== "NotAllowedError") return go();
      done = true;                // autoplay blocked: wait for a click
      this.current = null;
      this.audio = null;
      this.blocked = clip;
      this.needsGesture = true;
      this.emit();
    });
  }
}

export const player = new Player();
