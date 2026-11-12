import { beforeEach, describe, expect, it, vi } from "vitest";
import { Mic } from "./audio";

// Drive the voice-activity detector with synthetic audio, no browser needed.
const RATE = 16000;
const FRAME = 128;                     // AudioWorklet render quantum
const frameMs = (FRAME / RATE) * 1000; // 8 ms

type Internals = { frame(f: Float32Array): void; state: string; rate: number; ctx: unknown };

function tone(amplitude: number) {
  const f = new Float32Array(FRAME);
  for (let i = 0; i < FRAME; i++) f[i] = amplitude * Math.sin((2 * Math.PI * 220 * i) / RATE);
  return f;
}

function feed(mic: Mic, amplitude: number, ms: number) {
  const m = mic as unknown as Internals;
  for (let t = 0; t < ms; t += frameMs) m.frame(tone(amplitude));
}

// speech whose loudness glides from one amplitude to another (a sentence fading out)
function ramp(mic: Mic, from: number, to: number, ms: number) {
  const m = mic as unknown as Internals;
  const n = Math.round(ms / frameMs);
  for (let i = 0; i < n; i++) m.frame(tone(from + ((to - from) * i) / Math.max(1, n - 1)));
}

function makeMic(opts: Partial<Mic> = {}) {
  const onUtterance = vi.fn();
  const onSpeechStart = vi.fn();
  const mic = new Mic({ onUtterance, onSpeechStart, ...opts });
  const m = mic as unknown as Internals;
  m.rate = RATE;
  m.ctx = {};                          // pretend enable() ran
  m.state = mic.mode === "ptt" ? "ready" : "listening";
  return { mic, onUtterance, onSpeechStart };
}

const LOUD = 0.3, SOFT = 0.03, SILENT = 0;

describe("Mic voice activity detection", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("delivers one utterance after speech followed by a pause", async () => {
    const { mic, onUtterance, onSpeechStart } = makeMic();
    feed(mic, SILENT, 500);
    feed(mic, LOUD, 1200);
    expect(onSpeechStart).toHaveBeenCalledTimes(1);
    expect(mic.state).toBe("speaking");
    feed(mic, SILENT, 1400);
    expect(onUtterance).toHaveBeenCalledTimes(1);
    expect(mic.state).toBe("listening");

    const wav: Blob = onUtterance.mock.calls[0][0];
    const head = new DataView(await wav.arrayBuffer());
    expect(String.fromCharCode(head.getUint8(0), head.getUint8(1), head.getUint8(2), head.getUint8(3))).toBe("RIFF");
    expect(head.getUint32(24, true)).toBe(16000);           // resampled for Whisper
    const seconds = head.getUint32(40, true) / 2 / 16000;
    expect(seconds).toBeGreaterThan(1.2);                   // includes pre-roll + speech + trailing pause
    expect(seconds).toBeLessThan(3.2);
  });

  it("does not end the turn on a pause shorter than endSilenceMs", () => {
    const { mic, onUtterance } = makeMic({ endSilenceMs: 1300 });
    feed(mic, LOUD, 800);
    feed(mic, SILENT, 900);      // thinking pause mid-sentence
    feed(mic, LOUD, 800);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, SILENT, 1400);
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });

  it("ignores clicks and very short noises", () => {
    const { mic, onUtterance, onSpeechStart } = makeMic();
    feed(mic, LOUD, 80);         // shorter than the onset window
    feed(mic, SILENT, 1500);
    feed(mic, LOUD, 200);        // starts, but too short to be a real turn
    feed(mic, SILENT, 1500);
    expect(onSpeechStart).toHaveBeenCalledTimes(1);
    expect(onUtterance).not.toHaveBeenCalled();
  });

  it("needs louder speech to barge in while the interviewer is talking (echo guard)", () => {
    let playing = true;
    const { mic, onSpeechStart } = makeMic({ isPlaybackActive: () => playing });
    feed(mic, SOFT, 1000);       // speaker echo level: must not interrupt
    expect(onSpeechStart).not.toHaveBeenCalled();
    feed(mic, LOUD, 500);        // the candidate really talks over the interviewer
    expect(onSpeechStart).toHaveBeenCalledTimes(1);
    playing = false;
  });

  it("stays quiet while muted", () => {
    const { mic, onSpeechStart } = makeMic();
    mic.setMuted(true);
    feed(mic, LOUD, 1000);
    expect(onSpeechStart).not.toHaveBeenCalled();
    mic.setMuted(false);
    feed(mic, LOUD, 500);
    expect(onSpeechStart).toHaveBeenCalledTimes(1);
  });

  it("push-to-talk records exactly between start and stop", () => {
    const { mic, onUtterance } = makeMic({ mode: "ptt" });
    feed(mic, LOUD, 500);        // talking without pressing: ignored
    mic.startTalking();
    feed(mic, LOUD, 700);
    feed(mic, SILENT, 2000);     // pauses don't end a push-to-talk turn
    expect(onUtterance).not.toHaveBeenCalled();
    mic.stopTalking();
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });
});

describe("Adaptive end of turn", () => {
  it("ends a long answer that fades out after 1 s", () => {
    const { mic, onUtterance } = makeMic();
    feed(mic, LOUD, 3200);
    ramp(mic, LOUD, 0.02, 160);  // the last word trails off
    feed(mic, SILENT, 900);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, SILENT, 150);
    expect(onUtterance).toHaveBeenCalledTimes(1);
    const silenceMs = onUtterance.mock.calls[0][1].silenceMs;
    expect(silenceMs).toBeGreaterThanOrEqual(1000);
    expect(silenceMs).toBeLessThan(1100);
  });

  it.each([800, 1000, 1200])("does not cut a %i ms mid-thought pause after a phrase that stops abruptly", (pause) => {
    const { mic, onUtterance } = makeMic();
    feed(mic, LOUD, 3500);
    feed(mic, SILENT, pause);    // "...I'd put the events on a" (thinks) "queue"
    feed(mic, LOUD, 800);
    expect(onUtterance).not.toHaveBeenCalled();
  });

  it.each([800, 1000, 1200, 1300])("does not cut a %i ms pause after a short hesitant fragment", (pause) => {
    const { mic, onUtterance } = makeMic();
    feed(mic, LOUD, 3200);
    ramp(mic, LOUD, 0.02, 160);  // a finished sentence...
    feed(mic, SILENT, 500);      // a breath...
    feed(mic, LOUD, 400);        // "so I'd..."
    ramp(mic, LOUD, 0.02, 160);  // trailing off while thinking
    feed(mic, SILENT, pause);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, LOUD, 1000);       // "...put a queue in front of it"
    expect(onUtterance).not.toHaveBeenCalled();
  });

  it("the hesitant fragment still ends the turn after the long pause", () => {
    const { mic, onUtterance } = makeMic();
    feed(mic, LOUD, 3200);
    feed(mic, SILENT, 500);
    feed(mic, LOUD, 400);
    feed(mic, SILENT, 1700);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, SILENT, 200);
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });

  it("a fade after a short last stretch waits the normal pause", () => {
    const { mic, onUtterance } = makeMic();
    feed(mic, LOUD, 2500);
    feed(mic, SILENT, 500);
    feed(mic, LOUD, 1200);       // only 1.2 s since the last pause
    ramp(mic, LOUD, 0.02, 160);
    feed(mic, SILENT, 1200);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, SILENT, 200);
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });

  it("waits the normal pause when a long answer stops abruptly", () => {
    const { mic, onUtterance } = makeMic();
    feed(mic, LOUD, 3500);
    feed(mic, SILENT, 1250);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, SILENT, 150);
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });

  it("gives a short, abrupt start more time", () => {
    const { mic, onUtterance } = makeMic();
    feed(mic, LOUD, 600);        // "So I think..."
    feed(mic, SILENT, 1700);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, SILENT, 200);
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });

  it("a short answer that fades out waits the normal pause", () => {
    const { mic, onUtterance } = makeMic();
    feed(mic, LOUD, 700);
    ramp(mic, LOUD, 0.02, 160);  // "Yes, exactly."
    feed(mic, SILENT, 1250);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, SILENT, 150);
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });

  it("a fixed pause setting turns adaptivity off", () => {
    const { mic, onUtterance } = makeMic({ endSilenceMs: 1300 });
    feed(mic, LOUD, 3200);
    ramp(mic, LOUD, 0.02, 160);
    feed(mic, SILENT, 1250);
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, SILENT, 150);
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });

  it("speech under steady background noise is not mistaken for fading out", () => {
    const { mic, onUtterance } = makeMic();
    feed(mic, 0.01, 2000);       // room noise: the detector adapts to it
    feed(mic, LOUD, 3500);
    feed(mic, 0.01, 1250);       // back to the noise floor, abruptly
    expect(onUtterance).not.toHaveBeenCalled();
    feed(mic, 0.01, 150);
    expect(onUtterance).toHaveBeenCalledTimes(1);
  });
});

describe("Discarded speech", () => {
  it("reports a blip that was too short to be a turn", () => {
    const onDiscard = vi.fn();
    const { mic, onUtterance, onSpeechStart } = makeMic({ onDiscard });
    feed(mic, LOUD, 200);        // a cough: enough to start "speaking"...
    expect(onSpeechStart).toHaveBeenCalledTimes(1);
    feed(mic, SILENT, 2000);     // ...but not a turn
    expect(onUtterance).not.toHaveBeenCalled();
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("reports a recording dropped by muting", () => {
    const onDiscard = vi.fn();
    const { mic } = makeMic({ onDiscard });
    feed(mic, LOUD, 500);
    mic.setMuted(true);
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });
});
