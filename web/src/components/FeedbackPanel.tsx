import type { Feedback, SessionMeta } from "../api";
import { VERDICT_TONE, markdown } from "../lib/util";

interface Props {
  meta: SessionMeta;
  feedback: Feedback | null;
  onJump: (messageId: number) => void;
}

export default function FeedbackPanel({ meta, feedback: f, onJump }: Props) {
  if (meta.status === "evaluating") {
    return (
      <div className="evaluating">
        <span className="dot think" />
        <strong>Writing your evaluation…</strong>
        <span className="small">{meta.feedback_model} is reviewing the conversation and your sketches. This usually takes 1–3 minutes.</span>
      </div>
    );
  }
  if (!f) {
    return meta.status === "ended"
      ? <div className="evaluating">This interview hasn't been evaluated. Use “Evaluate” above if you want feedback on it.</div>
      : null;
  }
  const scores = f.scores || {};
  const keys = (meta.rubric?.length ? meta.rubric : Object.keys(scores)).filter((k) => k in scores);

  return (
    <div className="fb">
      <div className="verdict">
        <span className={`badge ${VERDICT_TONE[f.verdict || ""] || ""}`}>{f.verdict || "—"}</span>
        <span className="small muted">evaluated by {meta.feedback_model}</span>
      </div>
      {f.summary && <p>{f.summary}</p>}

      {keys.length > 0 && (
        <>
          <h3>Scores</h3>
          <div className="scores">
            {keys.map((k) => (
              <div className="score" key={k}>
                <span>{k}</span>
                <div className="bar5"><div style={{ width: `${(scores[k] ?? 0) * 20}%` }} /></div>
                <b>{scores[k] ?? "–"}</b>
              </div>
            ))}
          </div>
        </>
      )}

      {f.top3?.length ? (
        <>
          <h3>Top 3 to practice</h3>
          <ol className="top3">{f.top3.map((t, i) => <li key={i}>{t}</li>)}</ol>
        </>
      ) : null}

      {f.review?.length ? (
        <>
          <h3>To review</h3>
          <ul className="review">
            {f.review.map((r, i) => (
              <li key={i}>
                <button onClick={() => onJump(r.id)}>
                  <span className="topic">{r.topic}</span>
                  <span className="small">{r.what}</span>
                  <span className="ref">see moment #{r.id} →</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <div className="report" dangerouslySetInnerHTML={{ __html: markdown(f.report) }} />
    </div>
  );
}
