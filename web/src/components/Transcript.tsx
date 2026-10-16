import { useEffect, useLayoutEffect, useRef } from "react";
import type { Message } from "../api";
import { player } from "../lib/player";
import { markdown } from "../lib/util";

interface Props {
  messages: Message[];
  live: boolean;
  speakingId: number | null;
  flashId: number | null;
}

export default function Transcript({ messages, live, speakingId, flashId }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);   // follow new messages only if the user hasn't scrolled up

  useLayoutEffect(() => {
    if (stick.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (flashId == null) return;
    box.current?.querySelector(`[data-id="${flashId}"]`)?.scrollIntoView({ block: "center" });
  }, [flashId]);

  const visible = messages.filter((m) => !(m.role === "interviewer" && m.kind === "speech" && !m.text && m.done));

  return (
    <div
      className="log"
      ref={box}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
      }}
    >
      {visible.length === 0 && <div className="empty">The interviewer will start in a few seconds…</div>}
      {visible.map((m) => <Item key={m.id} m={m} live={live} speaking={m.id === speakingId} flash={m.id === flashId} />)}
    </div>
  );
}

function Item({ m, live, speaking, flash }: { m: Message; live: boolean; speaking: boolean; flash: boolean }) {
  if (m.role === "event") return <div className="divider" data-id={m.id}>Phase: {m.text}</div>;

  if (m.kind === "note") {
    return (
      <div className={`msg note${flash ? " flash" : ""}`} data-id={m.id}>
        <div className="who">Interviewer note</div>
        <div className="text" dangerouslySetInnerHTML={{ __html: markdown(m.text) }} />
      </div>
    );
  }

  const who = m.role === "interviewer" ? "Interviewer"
    : m.kind === "look" ? "You · 👀 whiteboard" : m.kind === "typed" ? "You · typed" : "You";
  const hasAudio = m.role === "interviewer" && m.audio && Object.keys(m.audio).length > 0;
  const cls = ["msg", m.role, m.interrupted && "interrupted", speaking && "speaking", flash && "flash"].filter(Boolean).join(" ");

  return (
    <div className={cls} data-id={m.id}>
      <div className="who">
        {who}
        {hasAudio && <button className="replay" title="Play" onClick={() => player.replay(m.audio!, m.id)}>▶</button>}
        {m.interrupted && <span>· interrupted</span>}
        <span className="msg-id">#{m.id}</span>
      </div>
      <div className={`text${m.role === "interviewer" && !m.done && live ? " typing" : ""}`}>{m.text}</div>
    </div>
  );
}
