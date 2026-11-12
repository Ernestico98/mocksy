// Interviewer clips that arrive while the candidate is talking are held, not dropped: the
// candidate may only have coughed. Once we know whether their speech became a real turn, the
// reply is either released (played as usual) or dropped and reported as unheard.

import type { Clip } from "./player";

export class HeldReplies {
  private clips: Clip[] = [];
  private dropped = new Set<number>();

  /** A clip of a reply already dropped as unheard: never play it, even if it arrives late. */
  isDropped(clip: Clip): boolean {
    return this.dropped.has(clip.message_id);
  }

  /** Hold this clip instead of playing it? While the candidate talks, and for the rest of a reply already held. */
  shouldHold(clip: Clip, candidateSpeaking: boolean): boolean {
    return candidateSpeaking || this.clips.some((c) => c.message_id === clip.message_id);
  }

  hold(clip: Clip) {
    this.clips.push(clip);
  }

  get size() { return this.clips.length; }

  /** The candidate barged in on this reply: drop it, including clips held or still to come. */
  dropReply(messageId: number) {
    this.dropped.add(messageId);
    this.clips = this.clips.filter((c) => c.message_id !== messageId);
  }

  /** The interview is stopping: forget what was held without playing it. */
  clear() {
    this.clips = [];
  }

  /** The candidate's speech was noise or empty: play what was held, in order. */
  release(): Clip[] {
    const out = this.clips.sort((a, b) => a.message_id - b.message_id || a.index - b.index);
    this.clips = [];
    return out;
  }

  /**
   * The candidate's speech became a turn: drop the held replies. Returns, per reply, the last
   * clip index the candidate heard (from `played`, or -1), for the interrupt call.
   */
  drop(played: Map<number, number>): { message_id: number; heard_index: number }[] {
    const ids = [...new Set(this.clips.map((c) => c.message_id))].sort((a, b) => a - b);
    ids.forEach((id) => this.dropped.add(id));
    this.clips = [];
    return ids.map((message_id) => ({ message_id, heard_index: played.get(message_id) ?? -1 }));
  }
}
