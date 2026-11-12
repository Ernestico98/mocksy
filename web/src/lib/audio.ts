// Microphone capture with a small energy-based voice activity detector.
// Hands-free: speech start/end is detected automatically. Push-to-talk: manual.
// Utterances are delivered as 16 kHz mono WAV blobs ready for Whisper.

const WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("capture", Capture);`;

export type MicState = "off" | "listening" | "ready" | "speaking" | "muted";
export type MicMode = "hands-free" | "ptt";

/**
 * Adaptive end of turn: how long a pause must last before the turn ends, chosen when the pause
 * starts. Conservative on purpose (being cut off is worse than waiting a bit):
 * - `shortMs` only after a long answer whose last stretch of speech was itself long and fades out
 *   like a finished sentence;
 * - `longMs` after a short, abrupt start ("So I think…") or a short fragment after an earlier pause
 *   ("…so I'd… [thinks]"), the typical shapes of a mid-thought pause;
 * - `baseMs` (the old fixed default) otherwise.
 */
export interface EndOfTurn {
  shortMs: number;
  baseMs: number;
  longMs: number;
  longSpeechMs: number;   // shortMs needs this much speech in the turn…
  longRunMs: number;      // …and this much since the last pause of ≥ RUN_GAP_MS
  shortSpeechMs: number;  // a turn shorter than this that stops abruptly gets longMs
  shortRunMs: number;     // a last stretch shorter than this after earlier speech gets longMs
  fadeRatio: number;      // "fades out": the last 120 ms are below this fraction of the 400 ms before
}

export const ADAPTIVE_END: EndOfTurn = {
  shortMs: 1000, baseMs: 1300, longMs: 1800,
  longSpeechMs: 3000, longRunMs: 2000, shortSpeechMs: 1000, shortRunMs: 700, fadeRatio: 0.6,
};

const TAIL_MS = 520, FADE_MS = 120;
const RUN_GAP_MS = 400;   // a pause at least this long starts a new stretch of speech

export class Mic {
  mode: MicMode = "hands-free";
  /** null: adaptive (see EndOfTurn); a number: a fixed pause in ms. */
  endSilenceMs: number | null = null;
  endOfTurn: EndOfTurn = ADAPTIVE_END;
  preRollMs = 350;
  state: MicState = "off";
  isPlaybackActive: () => boolean = () => false;
  onLevel: (level: number) => void = () => {};
  onState: (state: MicState) => void = () => {};
  onSpeechStart: () => void = () => {};
  /** `silenceMs`: the trailing pause waited before ending the turn (0 for push-to-talk). */
  onUtterance: (wav: Blob, info: { silenceMs: number }) => void = () => {};
  /** Speech started (onSpeechStart) but was too short to be a turn: a cough, a click. */
  onDiscard: () => void = () => {};

  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private rate = 48000;
  private noise = 0.004;
  private level = 0;
  private lastLevelEmit = 0;
  private pre: Float32Array[] = [];
  private rec: Float32Array[] | null = null;
  private loudMs = 0;
  private quietMs = 0;
  private speechMs = 0;
  private endMs = 0;                                   // pause that ends the current turn
  private runMs = 0;                                   // speech since the last pause ≥ RUN_GAP_MS
  private tail: { rms: number; ms: number }[] = [];    // the last voiced frames before a pause

  constructor(opts: Partial<Pick<Mic, "mode" | "endSilenceMs" | "onLevel" | "onState" | "onSpeechStart" | "onUtterance" | "onDiscard" | "isPlaybackActive">> = {}) {
    Object.assign(this, opts);
  }

  static supported(): boolean {
    return typeof navigator.mediaDevices?.getUserMedia === "function" && typeof AudioWorkletNode !== "undefined";
  }

  async enable() {
    if (this.ctx) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    const src = this.ctx.createMediaStreamSource(this.stream);
    const node = new AudioWorkletNode(this.ctx, "capture");
    const sink = this.ctx.createGain();
    sink.gain.value = 0;                       // keep the graph running, never play the mic back
    src.connect(node).connect(sink).connect(this.ctx.destination);
    node.port.onmessage = (e: MessageEvent<Float32Array>) => this.frame(e.data);
    this.rate = this.ctx.sampleRate;
    this.noise = 0.004;
    this.pre = [];
    this.rec = null;
    this.set(this.mode === "ptt" ? "ready" : "listening");
  }

  disable() {
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.ctx = null;
    this.dropRecording();
    this.set("off");
  }

  setMode(mode: MicMode) {
    this.mode = mode;
    this.dropRecording();
    if (this.ctx) this.set(mode === "ptt" ? "ready" : "listening");
  }

  /** How long the pause that just started must last to end the turn. */
  pauseToEndTurn(): number {
    if (this.endSilenceMs !== null) return this.endSilenceMs;
    const e = this.endOfTurn;
    const fades = this.fadesOut();
    if (fades && this.speechMs >= e.longSpeechMs && this.runMs >= e.longRunMs) return e.shortMs;
    if (this.speechMs < e.shortSpeechMs ? !fades : this.runMs < e.shortRunMs) return e.longMs;
    return e.baseMs;
  }

  private fadesOut(): boolean {
    let lastMs = 0, last = 0, beforeMs = 0, before = 0;
    for (let i = this.tail.length - 1; i >= 0; i--) {
      const { rms, ms } = this.tail[i];
      if (lastMs < FADE_MS) { lastMs += ms; last += rms * ms; } else { beforeMs += ms; before += rms * ms; }
    }
    if (beforeMs < 200) return false;     // not enough speech to tell
    return last / lastMs < (before / beforeMs) * this.endOfTurn.fadeRatio;
  }

  setMuted(muted: boolean) {
    if (!this.ctx) return;
    this.dropRecording();
    this.set(muted ? "muted" : this.mode === "ptt" ? "ready" : "listening");
  }

  private dropRecording() {
    if (this.rec) { this.rec = null; this.onDiscard(); }
  }

  // push-to-talk
  startTalking() {
    if (!this.ctx) return;
    this.rec = [];
    this.speechMs = 0;
    this.set("speaking");
    this.onSpeechStart();
  }

  stopTalking() {
    if (this.rec) this.finish(true);
  }

  private set(state: MicState) {
    this.state = state;
    this.onState(state);
  }

  private frame(f: Float32Array) {
    let sum = 0;
    for (let i = 0; i < f.length; i++) sum += f[i] * f[i];
    const rms = Math.sqrt(sum / f.length);
    const dt = (f.length / this.rate) * 1000;
    this.level = this.level * 0.85 + rms * 0.15;
    const now = performance.now();
    if (now - this.lastLevelEmit > 50) { this.lastLevelEmit = now; this.onLevel(this.level); }

    if (this.state === "muted" || this.state === "off") return;
    if (this.mode === "ptt") {
      if (this.rec) { this.rec.push(f); this.speechMs += dt; }
      return;
    }

    const playing = this.isPlaybackActive();
    // while the interviewer talks, require louder + longer speech so echo doesn't barge in
    const threshold = Math.max(0.012, this.noise * 3.2) * (playing ? 2.2 : 1);
    const needMs = playing ? 320 : 140;

    if (!this.rec) {
      this.pre.push(f);
      while (this.pre.length * f.length > (this.preRollMs / 1000) * this.rate) this.pre.shift();
      if (rms > threshold) {
        this.loudMs += dt;
        if (this.loudMs >= needMs) {
          this.rec = this.pre.splice(0);
          this.speechMs = this.loudMs;
          this.quietMs = 0;
          this.runMs = this.loudMs;
          this.tail = [];
          this.set("speaking");
          this.onSpeechStart();
        }
      } else {
        this.loudMs = Math.max(0, this.loudMs - dt);
        if (!playing) this.noise = this.noise * 0.995 + rms * 0.005;
      }
      return;
    }

    this.rec.push(f);
    if (rms > threshold * 0.6) {
      if (this.quietMs >= RUN_GAP_MS) { this.runMs = 0; this.tail = []; }
      this.quietMs = 0;
      this.speechMs += dt;
      this.runMs += dt;
      this.tail.push({ rms, ms: dt });
      let tailMs = this.tail.reduce((n, t) => n + t.ms, 0);
      while (tailMs - this.tail[0].ms >= TAIL_MS) tailMs -= this.tail.shift()!.ms;
    } else {
      if (this.quietMs === 0) this.endMs = this.pauseToEndTurn();
      this.quietMs += dt;
    }
    const totalMs = (this.rec.length * f.length / this.rate) * 1000;
    if ((this.quietMs > 0 && this.quietMs >= this.endMs) || totalMs > 180000) this.finish(false);
  }

  private finish(manual: boolean) {
    const frames = this.rec;
    this.rec = null;
    this.loudMs = 0;
    this.set(this.mode === "ptt" ? "ready" : "listening");
    if (!frames || this.speechMs < (manual ? 200 : 350)) return this.onDiscard(); // too short to be speech
    this.onUtterance(encodeWav(frames, this.rate), { silenceMs: manual ? 0 : this.quietMs });
  }
}

function encodeWav(frames: Float32Array[], rate: number): Blob {
  const len = frames.reduce((n, f) => n + f.length, 0);
  const all = new Float32Array(len);
  let o = 0;
  for (const f of frames) { all.set(f, o); o += f.length; }
  const target = 16000;
  const ratio = rate / target;
  const n = Math.floor(len / ratio);
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * ratio), b = Math.min(len, Math.floor((i + 1) * ratio));
    let s = 0;
    for (let j = a; j < b; j++) s += all[j];
    const v = Math.max(-1, Math.min(1, s / Math.max(1, b - a)));
    pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const dv = new DataView(buf);
  const str = (off: number, s: string) => [...s].forEach((c, i) => dv.setUint8(off + i, c.charCodeAt(0)));
  str(0, "RIFF"); dv.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE");
  str(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, target, true); dv.setUint32(28, target * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  str(36, "data"); dv.setUint32(40, pcm.length * 2, true);
  new Int16Array(buf, 44).set(pcm);
  return new Blob([buf], { type: "audio/wav" });
}
