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

export class Mic {
  mode: MicMode = "hands-free";
  endSilenceMs = 1300;
  preRollMs = 350;
  state: MicState = "off";
  isPlaybackActive: () => boolean = () => false;
  onLevel: (level: number) => void = () => {};
  onState: (state: MicState) => void = () => {};
  onSpeechStart: () => void = () => {};
  /** `silenceMs`: the trailing pause waited before ending the turn (0 for push-to-talk). */
  onUtterance: (wav: Blob, info: { silenceMs: number }) => void = () => {};

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

  constructor(opts: Partial<Pick<Mic, "mode" | "endSilenceMs" | "onLevel" | "onState" | "onSpeechStart" | "onUtterance" | "isPlaybackActive">> = {}) {
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
    this.rec = null;
    this.set("off");
  }

  setMode(mode: MicMode) {
    this.mode = mode;
    this.rec = null;
    if (this.ctx) this.set(mode === "ptt" ? "ready" : "listening");
  }

  setMuted(muted: boolean) {
    if (!this.ctx) return;
    this.rec = null;
    this.set(muted ? "muted" : this.mode === "ptt" ? "ready" : "listening");
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
    if (rms > threshold * 0.6) { this.quietMs = 0; this.speechMs += dt; }
    else this.quietMs += dt;
    const totalMs = (this.rec.length * f.length / this.rate) * 1000;
    if (this.quietMs >= this.endSilenceMs || totalMs > 180000) this.finish(false);
  }

  private finish(manual: boolean) {
    const frames = this.rec;
    this.rec = null;
    this.loudMs = 0;
    this.set(this.mode === "ptt" ? "ready" : "listening");
    if (!frames || this.speechMs < (manual ? 200 : 350)) return; // too short to be speech
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
