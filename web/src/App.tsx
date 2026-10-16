import { useEffect, useState } from "react";
import { api, subscribe, type AppMeta } from "./api";
import { ConfirmProvider } from "./components/Confirm";
import Home from "./pages/Home";
import SessionView from "./pages/SessionView";

// Tiny hash router: "#/" home, "#/s/<id>" a session.
function useRoute() {
  const read = () => {
    const m = location.hash.match(/^#\/s\/(.+)$/);
    return m ? decodeURIComponent(m[1]) : null;
  };
  const [sessionId, setSessionId] = useState(read);
  useEffect(() => {
    const on = () => setSessionId(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return sessionId;
}

export default function App() {
  const sessionId = useRoute();
  const [meta, setMeta] = useState<AppMeta | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.meta().then(setMeta).catch((e: Error) => setError(e.message));
    return subscribe((ev) => {
      if (ev.type === "settings") setMeta((m) => (m ? { ...m, settings: ev.settings } : m));
    });
  }, []);

  if (error) return <div className="boot">Can't reach the server ({error}). Is <code>./run.sh</code> running?</div>;
  if (!meta) return <div className="boot">Loading…</div>;

  return (
    <ConfirmProvider>
      {sessionId ? <SessionView key={sessionId} id={sessionId} meta={meta} /> : <Home meta={meta} />}
    </ConfirmProvider>
  );
}
