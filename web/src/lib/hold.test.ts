import { describe, expect, it } from "vitest";
import { HeldReplies } from "./hold";

const clip = (message_id: number, index: number) => ({ message_id, index, url: `/a/${message_id}-${index}.wav` });

describe("Replies held while the candidate talks", () => {
  it("holds clips while the candidate speaks, and the rest of a held reply after", () => {
    const h = new HeldReplies();
    expect(h.shouldHold(clip(5, 0), false)).toBe(false);   // nobody talking: play
    expect(h.shouldHold(clip(5, 0), true)).toBe(true);
    h.hold(clip(5, 0));
    expect(h.shouldHold(clip(5, 1), false)).toBe(true);    // same reply: keep it together
    expect(h.shouldHold(clip(6, 0), false)).toBe(false);
  });

  it("a cough releases the reply, in order, so it plays as usual", () => {
    const h = new HeldReplies();
    h.hold(clip(5, 1));
    h.hold(clip(5, 0));
    expect(h.release().map((c) => c.index)).toEqual([0, 1]);
    expect(h.size).toBe(0);
  });

  it("a real turn drops the reply and reports what was heard of it", () => {
    const h = new HeldReplies();
    h.hold(clip(5, 2));
    h.hold(clip(5, 3));
    h.hold(clip(7, 0));
    const played = new Map([[5, 1]]);                       // clips 0-1 of reply 5 had played
    expect(h.drop(played)).toEqual([{ message_id: 5, heard_index: 1 }, { message_id: 7, heard_index: -1 }]);
    expect(h.size).toBe(0);
    expect(h.release()).toEqual([]);
    expect(h.isDropped(clip(5, 4))).toBe(true);            // a late clip of a dropped reply
    expect(h.isDropped(clip(8, 0))).toBe(false);
  });

  it("a barge-in drops that reply's held and future clips", () => {
    const h = new HeldReplies();
    h.hold(clip(5, 2));
    h.hold(clip(6, 0));
    h.dropReply(5);
    expect(h.isDropped(clip(5, 3))).toBe(true);
    expect(h.release().map((c) => c.message_id)).toEqual([6]);
  });

  it("clearing (the interview stops) forgets held clips without playing them", () => {
    const h = new HeldReplies();
    h.hold(clip(5, 0));
    h.clear();
    expect(h.release()).toEqual([]);
  });
});
